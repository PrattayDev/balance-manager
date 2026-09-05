from flask import Flask, render_template, request, redirect, session, url_for, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from datetime import datetime
import requests
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
        # Fetch oldest-first so we can build a correct running balance,
        # then re-order for display afterwards.
        cur.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY date ASC, id ASC", (current_user_id,))
        all_transactions = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        all_transactions = []
        print(e)

    # Attach a true, all-time running balance to every transaction, in
    # chronological order, before any month filtering happens.
    running_balance = 0.0
    enriched = []
    available_months = []
    for t in all_transactions:
        row = dict(t)
        if row['type'] == 'Credit':
            running_balance += row['amount']
        elif row['type'] == 'Debit':
            running_balance -= row['amount']
        row['running_balance'] = round(running_balance, 2)
        enriched.append(row)
        month_key = (row['date'] or '')[:7]
        if month_key and month_key not in available_months:
            available_months.append(month_key)

    available_months.sort(reverse=True)

    def month_label(key):
        try:
            return datetime.strptime(key + '-01', '%Y-%m-%d').strftime('%B %Y')
        except Exception:
            return key

    month_options = [{'value': m, 'label': month_label(m)} for m in available_months]

    # Monthly income-vs-expense trend, across the FULL history regardless of
    # any month filter, so the trend chart always shows the complete picture.
    monthly_totals = {}
    for row in enriched:
        month_key = (row['date'] or '')[:7]
        if not month_key:
            continue
        if month_key not in monthly_totals:
            monthly_totals[month_key] = {'credit': 0.0, 'debit': 0.0}
        if row['type'] == 'Credit':
            monthly_totals[month_key]['credit'] += row['amount']
        elif row['type'] == 'Debit':
            monthly_totals[month_key]['debit'] += row['amount']

    trend_months_sorted = sorted(monthly_totals.keys())  # chronological, oldest first
    trend_labels = [month_label(m) for m in trend_months_sorted]
    trend_income = [round(monthly_totals[m]['credit'], 2) for m in trend_months_sorted]
    trend_expenses = [round(monthly_totals[m]['debit'], 2) for m in trend_months_sorted]

    # The true, all-time balance (unaffected by any month filter)
    total_balance = running_balance

    selected_month = request.args.get('month', 'all')
    if selected_month != 'all' and selected_month not in available_months:
        selected_month = 'all'

    selected_month_label = 'All Time' if selected_month == 'all' else month_label(selected_month)

    if selected_month == 'all':
        filtered = enriched
    else:
        filtered = [t for t in enriched if (t['date'] or '')[:7] == selected_month]

    # Display transactions newest-first
    transactions = list(reversed(filtered))

    period_credit = 0.0
    period_debit = 0.0
    expenses_by_category = {}

    for t in filtered:
        amount = t['amount']
        if t['type'] == 'Credit':
            period_credit += amount
        elif t['type'] == 'Debit':
            period_debit += amount
            category = t['category']
            if category not in expenses_by_category:
                expenses_by_category[category] = 0.0
            expenses_by_category[category] += amount

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

    # Balance-over-time chart uses the TRUE cumulative running balance,
    # just restricted to points that fall within the selected period, so
    # the trend line during that month is accurate rather than reset to 0.
    timeline_dates = [t['date'] for t in filtered]
    timeline_balances = [t['running_balance'] for t in filtered]

    return render_template(
        'index.html',
        transactions=transactions,
        balance=total_balance,
        total_credit=period_credit,
        total_debit=period_debit,
        pie_labels=labels,
        pie_sizes=sizes,
        timeline_dates=timeline_dates,
        timeline_balances=timeline_balances,
        username=session['username'],
        currency_symbol=currency_symbol,
        user_currency=user_currency,
        month_options=month_options,
        selected_month=selected_month,
        selected_month_label=selected_month_label,
        trend_labels=trend_labels,
        trend_income=trend_income,
        trend_expenses=trend_expenses
    )

@app.route('/insights', methods=['POST'])
def insights():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    current_user_id = session['user_id']
    user_currency = session.get('currency', 'USD')
    currency_symbol = CURRENCY_SYMBOLS.get(user_currency, '$')
    selected_month = request.args.get('month', 'all')

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY date ASC, id ASC", (current_user_id,))
        all_transactions = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(e)
        return jsonify({'reply': "Couldn't load your transactions right now. Please try again shortly."})

    if selected_month == 'all':
        scoped = all_transactions
    else:
        scoped = [t for t in all_transactions if (t['date'] or '')[:7] == selected_month]

    if len(scoped) == 0:
        return jsonify({'reply': "There's no transaction data for this period yet, so there's nothing to summarize. Add a few transactions and check back."})

    total_credit = sum(t['amount'] for t in scoped if t['type'] == 'Credit')
    total_debit = sum(t['amount'] for t in scoped if t['type'] == 'Debit')

    category_totals = {}
    for t in scoped:
        if t['type'] == 'Debit':
            category_totals[t['category']] = category_totals.get(t['category'], 0) + t['amount']
    top_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:6]

    period_label = selected_month if selected_month != 'all' else 'all-time'
    summary_lines = [
        f"Period: {period_label}",
        f"Total income: {currency_symbol}{total_credit:.2f}",
        f"Total expenses: {currency_symbol}{total_debit:.2f}",
        f"Net: {currency_symbol}{(total_credit - total_debit):.2f}",
        "Top spending categories:"
    ]
    for cat, amt in top_categories:
        summary_lines.append(f"  - {cat}: {currency_symbol}{amt:.2f}")
    summary_text = "\n".join(summary_lines)

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return jsonify({'reply': "AI Insights isn't set up yet. Add a GEMINI_API_KEY environment variable in your Vercel project settings to enable this feature (free from ai.google.dev)."})

    prompt = (
        "You are a friendly, practical personal finance assistant inside a budgeting app called Balance Manager. "
        "Given the transaction summary below, respond with two short parts:\n"
        "1. A brief 2-3 sentence summary of the spending pattern.\n"
        "2. Three specific, actionable savings tips based on the actual top spending categories shown. "
        "Be concrete, not generic. Keep the whole reply under 150 words, no markdown headers, plain short paragraphs.\n\n"
        f"Transaction summary:\n{summary_text}"
    )

    try:
        # NOTE: Google has announced gemini-2.5-flash will be phased out around
        # October 2026, replaced by gemini-3.5-flash. If this stops working after
        # that date, update the model name below (check ai.google.dev/gemini-api/docs/changelog
        # for current free-tier model availability).
        response = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            return jsonify({'reply': "I couldn't generate insights for that period — try again, or pick a different month."})
        parts = candidates[0].get("content", {}).get("parts", [])
        reply_text = "".join(p.get("text", "") for p in parts).strip()
        if not reply_text:
            reply_text = "I couldn't generate insights right now — please try again in a moment."
        return jsonify({'reply': reply_text})
    except Exception as e:
        print("AI insights error:", e)
        return jsonify({'reply': "Something went wrong generating insights. Please try again shortly."})

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

@app.route('/delete/<int:transaction_id>', methods=['POST'])
def delete(transaction_id):
    if 'user_id' not in session:
        return redirect('/login')

    current_user_id = session['user_id']

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # The user_id check matters: it ensures a user can only ever delete
        # their own rows, even if someone crafts a request with another id.
        cur.execute(
            "DELETE FROM transactions WHERE id = %s AND user_id = %s",
            (transaction_id, current_user_id)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print("Delete failed:", e)

    # Send the user back to the month they were viewing
    month = request.form.get('month', 'all')
    if month and month != 'all':
        return redirect('/?month=' + month)
    return redirect('/')

# ============================================================================
# SMS -> auto transaction webhook  (paste this block into api/index.py)
#
# Place it ABOVE the `if __name__ == '__main__':` line, alongside your other
# @app.route definitions. It needs the Flask `app`, `get_db_connection`, `re`,
# `os`, `datetime`, and `jsonify` that your file already imports/defines.
#
# HOW IT WORKS
#   Your phone forwards each bank SMS to  /sms-webhook?token=YOUR_SECRET
#   This route parses it and inserts a transaction for the right user — no
#   manual typing. Unparseable messages are ignored (200 OK) so the forwarder
#   app doesn't keep retrying.
#
# SETUP (two environment variables on Vercel)
#   SMS_WEBHOOK_TOKEN  = a long random string you invent (the shared password)
#   SMS_WEBHOOK_USER_ID = the id of the user row these transactions belong to
#                         (look it up once in your DB: SELECT id, username FROM users;)
# ============================================================================

import re
from datetime import datetime

_CREDIT_WORDS = ['credited', 'credit', 'received', 'deposit', 'refund']
_DEBIT_WORDS  = ['purchase', 'debited', 'debit', 'withdrawn', 'payment', 'spent']


def parse_bank_sms(text):
    """Return {'type','amount','date','category'} or None if it isn't a txn SMS."""
    if not text:
        return None
    t = text.strip()
    low = t.lower()

    # --- amount: first "BDT <number>" is the transaction amount ---
    m = re.search(r'BDT\s*([\d,]+(?:\.\d+)?)', t, re.IGNORECASE)
    if not m:
        return None
    amount = float(m.group(1).replace(',', ''))

    # --- type: debit words take priority if both appear ---
    ttype = None
    if any(w in low for w in _CREDIT_WORDS):
        ttype = 'Credit'
    if any(w in low for w in _DEBIT_WORDS):
        ttype = 'Debit'
    if ttype is None:
        return None

    # --- date: dd-Mon-yy / dd-Mon-yyyy (falls back to today) ---
    date_iso = datetime.now().strftime('%Y-%m-%d')
    dm = re.search(r'(\d{1,2}-[A-Za-z]{3}-\d{2,4})', t)
    if dm:
        for fmt in ('%d-%b-%y', '%d-%b-%Y'):
            try:
                date_iso = datetime.strptime(dm.group(1), fmt).strftime('%Y-%m-%d')
                break
            except ValueError:
                continue

    # --- category / merchant ---
    category = 'Other'
    fm = re.search(r'from\s+([A-Z0-9 .&*/-]+?)\.?(?:Card|\s+on\b)', t, re.IGNORECASE)
    if fm:
        category = fm.group(1).strip().title()
    elif 'interest' in low:
        category = 'Interest'
    elif ttype == 'Credit':
        category = 'Income'

    return {'type': ttype, 'amount': amount, 'date': date_iso, 'category': category}


@app.route('/sms-webhook', methods=['POST'])
def sms_webhook():
    # 1) shared-secret check — stops strangers posting fake transactions
    expected = os.environ.get('SMS_WEBHOOK_TOKEN')
    supplied = request.args.get('token') or request.headers.get('X-Token')
    if not expected or supplied != expected:
        return jsonify({'error': 'unauthorized'}), 401

    user_id = os.environ.get('SMS_WEBHOOK_USER_ID')
    if not user_id:
        return jsonify({'error': 'SMS_WEBHOOK_USER_ID not set'}), 500

    # 2) get the SMS text — accept form field, JSON, or raw body
    body = ''
    if request.form.get('message'):
        body = request.form.get('message')
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        body = data.get('message', '') or data.get('text', '')
    if not body:
        body = request.get_data(as_text=True) or ''

    parsed = parse_bank_sms(body)
    print(f"SMS-WEBHOOK body={body!r} parsed={parsed}")
    if not parsed:
        # Not a transaction SMS (OTP, promo, etc.) — acknowledge so the phone
        # app stops retrying, but insert nothing.
        return jsonify({'status': 'ignored'}), 200

    # 3) duplicate guard — the same SMS forwarded twice shouldn't double-count
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT id FROM transactions
               WHERE user_id = %s AND type = %s AND amount = %s AND date = %s
               AND category = %s LIMIT 1""",
            (user_id, parsed['type'], parsed['amount'], parsed['date'], parsed['category'])
        )
        if cur.fetchone():
            cur.close(); conn.close()
            return jsonify({'status': 'duplicate'}), 200

        cur.execute(
            "INSERT INTO transactions (user_id, type, amount, category, date) VALUES (%s, %s, %s, %s, %s)",
            (user_id, parsed['type'], parsed['amount'], parsed['category'], parsed['date'])
        )
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        print("sms-webhook DB error:", e)
        return jsonify({'error': 'db'}), 500

    return jsonify({'status': 'added', 'transaction': parsed}), 200