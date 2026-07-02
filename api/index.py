from flask import Flask, render_template, request, redirect
import psycopg2
import psycopg2.extras
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
from io import BytesIO
import os

app = Flask(__name__, template_folder='../templates')

def get_db_connection():
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise Exception("DATABASE_URL environment variable is missing.")
    # FIXED: Added sslmode='require' to allow encrypted connections to Vercel Postgres
    conn = psycopg2.connect(db_url, cursor_factory=psycopg2.extras.DictCursor, sslmode='require')
    return conn

# Vercel functions are serverless; we ensure the table exists safely on boot
try:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            type TEXT,
            amount REAL,
            category TEXT,
            date TEXT
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()
except Exception as e:
    print("Database init pending or failed:", e)

@app.route('/')
def index():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions ORDER BY date DESC")
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

    # 1. Generate the "Where" Pie Chart
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

    # 2. Generate the "When" Timeline Chart
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
        plt.ylabel('Balance ($)')
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
        line_chart_url=line_chart_url
    )

@app.route('/add', methods=['POST'])
def add():
    t_type = request.form['type']
    amount = float(request.form['amount'])
    category = request.form['category']
    date = request.form['date']
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO transactions (type, amount, category, date) VALUES (%s, %s, %s, %s)", 
        (t_type, amount, category, date)
    )
    conn.commit()
    cur.close()
    conn.close()
    
    return redirect('/')