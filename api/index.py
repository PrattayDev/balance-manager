from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
from io import BytesIO
import os

app = Flask(__name__, template_folder='../templates')
app.secret_key = os.environ.get('SECRET_KEY', 'default-super-secret-key-change-me')

def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise Exception("DATABASE_URL environment variable is missing.")
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.DictCursor, sslmode='require')
    return conn

# Ensure tables and new columns exist safely on Vercel boot
try:
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            currency TEXT DEFAULT 'USD'
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            type TEXT,
            amount REAL,
            category TEXT,
            date TEXT
        )
    ''')
    
    # Safely add columns if they don't exist for older accounts
    cur.execute('ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_id INTEGER;')
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'USD';")
    
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print("Database init pending or failed:", e)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        currency = request.form.get('currency', 'USD')
        hashed_password = generate_password_hash(password)
        
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("INSERT INTO users (username, password, currency) VALUES (%s, %s, %s)", (username, hashed_password, currency))
            conn.commit()
            cur.close()
            conn.close()
            return redirect('/login')
        except Exception:
            return render_template('signup.html', error="Username already exists!")
            
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['currency'] = user['currency'] if user['currency'] else 'USD'
            return redirect('/')
        else:
            return render_template('login.html', error="Invalid username or password.")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# --- NEW: Route to change currency on the fly ---
@app.route('/settings', methods=['POST'])
def settings():
    if 'user_id' not in session:
        return redirect('/login')
        
    new_currency = request.form.get('currency', 'USD')
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET currency = %s WHERE id = %s", (new_currency, session['user_id']))
    conn.commit()
    cur.close()
    conn.close()
    
    session['currency'] = new_currency
    return redirect('/')

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect('/login')
        
    current_user_id = session['user_id']
    user_currency = session.get('currency', 'USD')
    
    # Map the currency code to its symbol
    currency_symbols = {'USD': '$', 'BDT': '৳', 'INR': '₹'}
    currency_symbol = currency_symbols.get(user_currency, '$')
    
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY date DESC", (current_user_id,))
        transactions = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        transactions = []
        print(e)
    
    total_balance = 0.0
    expenses_by_category = {}
    
    for t in transactions:
        amount = t['amount']
        if t['type'] == 'Credit':
            total_balance = total_balance + amount
        elif t['type'] == 'Debit':
            total_balance = total_balance - amount
            
            category = t['category']
            if category not in expenses_by_category:
                expenses_by_category[category] = 0.0
            expenses_by_category[category] = expenses_by_category[category] + amount

    labels = []
    sizes = []
    for cat, amt in expenses_by_category.items():
        labels.append(cat)
        sizes.append(amt)

    chart_url = None
    if len(sizes) > 0:
        plt.figure(figsize=(5, 5))
        plt.pie(sizes, labels=labels, autopct='%1.1f%%', startangle=140)
        plt.title('Expense Breakdown')
        
        img = BytesIO()
        plt.savefig(img, format='png', transparent=True)
        img.seek(0)
        chart_url = base64.b64encode(img.getvalue()).decode()
        plt.close()

    timeline_dates = []
    timeline_balances = []
    running_balance = 0.0
    
    reversed_transactions = []
    for t in transactions:
        reversed_transactions.insert(0, t)
        
    for t in reversed_transactions:
        if t['type'] == 'Credit':
            running_balance = running_balance + t['amount']
        elif t['type'] == 'Debit':
            running_balance = running_balance - t['amount']
            
        timeline_dates.append(t['date'])
        timeline_balances.append(running_balance)

    line_chart_url = None
    if len(timeline_dates) > 0:
        plt.figure(figsize=(7, 4))
        plt.plot(timeline_dates, timeline_balances, marker='o', color='#2ecc71', linewidth=2)
        plt.title('Balance Over Time')
        plt.xlabel('Date')
        # Display the chosen currency on the chart's side label!
        plt.ylabel(f'Balance ({user_currency})') 
        plt.xticks(rotation=45)
        plt.tight_layout()
        
        img2 = BytesIO()
        plt.savefig(img2, format='png', transparent=True)
        img2.seek(0)
        line_chart_url = base64.b64encode(img2.getvalue()).decode()
        plt.close()

    return render_template(
        'index.html', 
        transactions=transactions, 
        balance=total_balance, 
        chart_url=chart_url, 
        line_chart_url=line_chart_url,
        username=session['username'],
        currency_symbol=currency_symbol,
        user_currency=user_currency
    )

@app.route('/add', methods=['POST'])
def add():
    if 'user_id' not in session:
        return redirect('/login')
        
    t_type = request.form['type']
    amount = float(request.form['amount'])
    category = request.form['category']
    date = request.form['date']
    current_user_id = session['user_id']
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (user_id, type, amount, category, date) VALUES (%s, %s, %s, %s, %s)", 
        (current_user_id, t_type, amount, category, date)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    return redirect('/')