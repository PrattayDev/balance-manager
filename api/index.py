from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
import os

app = Flask(__name__, template_folder='../templates')
app.secret_key = os.environ.get('SECRET_KEY', 'default-super-secret-key-change-me')

# Shared currency -> symbol map used across routes
CURRENCY_SYMBOLS = {
    'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥', 'CNY': '¥',
    'INR': '₹', 'BDT': '৳', 'PKR': '₨', 'LKR': 'Rs', 'NPR': '₨',
    'AED': 'د.إ', 'SAR': '﷼', 'QAR': 'ر.ق', 'KWD': 'د.ك',
    'SGD': 'S$', 'MYR': 'RM', 'THB': '฿', 'IDR': 'Rp', 'PHP': '₱',
    'VND': '₫', 'KRW': '₩', 'HKD': 'HK$', 'TWD': 'NT$',
    'AUD': 'A$', 'NZD': 'NZ$', 'CAD': 'C$', 'CHF': 'CHF',
    'BRL': 'R$', 'MXN': 'Mex$', 'ARS': 'AR$', 'ZAR': 'R',
    'NGN': '₦', 'KES': 'KSh', 'EGP': 'E£', 'TRY': '₺', 'RUB': '₽',
}

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
    currency_symbol = CURRENCY_SYMBOLS.get(user_currency, '$')
    
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
    total_credit = 0.0
    total_debit = 0.0
    expenses_by_category = {}
    
    for t in transactions:
        amount = t['amount']
        if t['type'] == 'Credit':
            total_balance = total_balance + amount
            total_credit = total_credit + amount
        elif t['type'] == 'Debit':
            total_balance = total_balance - amount
            total_debit = total_debit + amount
            
            category = t['category']
            if category not in expenses_by_category:
                expenses_by_category[category] = 0.0
            expenses_by_category[category] = expenses_by_category[category] + amount

    # Sort categories by spend, keep the top 6, roll the rest into "Other"
    # so the legend stays readable on small screens
    sorted_categories = sorted(expenses_by_category.items(), key=lambda x: x[1], reverse=True)
    MAX_SLICES = 6
    labels = []
    sizes = []
    if len(sorted_categories) > MAX_SLICES:
        top = sorted_categories[:MAX_SLICES - 1]
        rest = sorted_categories[MAX_SLICES - 1:]
        for cat, amt in top:
            labels.append(cat)
            sizes.append(round(amt, 2))
        other_total = sum(amt for _, amt in rest)
        labels.append('Other')
        sizes.append(round(other_total, 2))
    else:
        for cat, amt in sorted_categories:
            labels.append(cat)
            sizes.append(round(amt, 2))

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
        timeline_balances.append(round(running_balance, 2))

    return render_template(
        'index.html',
        transactions=transactions,
        balance=total_balance,
        total_credit=total_credit,
        total_debit=total_debit,
        pie_labels=labels,
        pie_sizes=sizes,
        timeline_dates=timeline_dates,
        timeline_balances=timeline_balances,
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