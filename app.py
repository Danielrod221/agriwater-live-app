import os
import psycopg2
from psycopg2.extras import DictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_bcrypt import Bcrypt
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from fpdf import FPDF
from datetime import datetime, timedelta
import stripe
import requests

# --- App Configuration ---
DATABASE_URL = os.environ.get('DATABASE_URL')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
SIGNWELL_API_KEY = os.environ.get('SIGNWELL_API_KEY')
YOUR_DOMAIN = 'https://www.agriwatermarketplace.com'

app = Flask(__name__)
# CORRECTED: Use a permanent secret key from environment variables
app.secret_key = os.environ.get('SECRET_KEY')
stripe.api_key = STRIPE_SECRET_KEY
bcrypt = Bcrypt(app)

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

# --- Helper Functions ---

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def send_email(to_email, subject, html_content):
    message = Mail(from_email='support@agriwatermarketplace.com', to_emails=to_email, subject=subject, html_content=html_content)
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        print(f"Email sent to {to_email}, Status Code: {response.status_code}")
    except Exception as e:
        print(f"Error sending email: {e}")

def create_lease_agreement(listing, seller, buyer):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(200, 10, txt="Temporary Water Lease Agreement", ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    today_date = datetime.now().strftime("%B %d, %Y")
    pdf.multi_cell(0, 5, f"This agreement is made on {today_date} between the following parties:")
    pdf.ln(5)
    pdf.multi_cell(0, 5, f"SELLER: {seller['name']} ({seller['email']})")
    pdf.multi_cell(0, 5, f"BUYER: {buyer['name']} ({buyer['email']})")
    pdf.ln(10)
    pdf.multi_cell(0, 5, f"The Seller agrees to lease and the Buyer agrees to receive the following water allocation for the duration of: {listing['lease_duration']}.")
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(40, 10, 'Water District:', 1); pdf.cell(0, 10, f" {listing['water_district']}", 1, ln=True)
    pdf.cell(40, 10, 'Amount:', 1); pdf.cell(0, 10, f" {listing['amount_af']} Acre-Feet", 1, ln=True)
    total_price = float(listing['price_per_af']) * float(listing['amount_af'])
    pdf.cell(40, 10, 'Total Price:', 1); pdf.cell(0, 10, f" ${total_price:,.2f}", 1, ln=True)
    pdf.ln(10)
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 5, "This agreement serves as a formal request to the specified water district to make the necessary ledger adjustments for this temporary transfer.")
    pdf.ln(20)
    pdf.cell(90, 10, "Seller Signature:", 0, 0); pdf.cell(90, 10, "Buyer Signature:", 0, 1)
    pdf.ln(10)
    pdf.cell(90, 10, "_________________________"); pdf.cell(90, 10, "_________________________")
    return None

def send_for_signature(pdf_path, seller, buyer):
    if not pdf_path or not SIGNWELL_API_KEY:
        print("PDF path not provided or SignWell API Key not set. Skipping signature.")
        return False
    
    headers = {'X-Api-Key': SIGNWELL_API_KEY}
    files = {'files[]': (os.path.basename(pdf_path), open(pdf_path, 'rb'), 'application/pdf')}
    upload_url = "https://www.signwell.com/api/v1/document_templates/"
    upload_response = requests.post(upload_url, headers=headers, files=files)
    if upload_response.status_code != 201:
        print(f"SignWell Upload Error: {upload_response.text}"); return False
    template_id = upload_response.json()['id']
    payload = {
        "document_template_ids": [template_id],
        "recipients": [{"email": seller['email'], "name": seller['name'], "role": "Seller"}, {"email": buyer['email'], "name": buyer['name'], "role": "Buyer"}],
        "name": f"Water Lease Agreement - {seller['name']} & {buyer['name']}",
        "subject": "Action Required: Sign Your Water Lease Agreement",
        "message": "Please sign the attached water lease agreement to finalize your transaction on the Agri-Water Marketplace."
    }
    request_url = "https://www.signwell.com/api/v1/document_requests/"
    request_response = requests.post(request_url, headers=headers, json=payload)
    if request_response.status_code == 201:
        print("Successfully sent document for signature via SignWell."); return True
    else:
        print(f"SignWell Request Error: {request_response.text}"); return False

def get_success_lake_data():
    """
    Fetches the latest reservoir storage data for Success Lake from the CDEC API.
    """
    try:
        today = datetime.now()
        yesterday = today - timedelta(days=7) # Look back 7 days
        start_date = yesterday.strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        
        api_url = f"http://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet?Stations=SUC&SensorNums=15&dur_code=D&Start={start_date}&End={end_date}"
        
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data:
            latest_reading = data[-1]
            value = latest_reading.get('value')
            
            if value is not None and value > -9999:
                return {
                    "date": latest_reading.get('date'),
                    "value": int(value)
                }
        return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data from CDEC API for Success Lake: {e}")
        return None

# --- Route Definitions ---
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']; email = request.form['email']; password = request.form['password']; phone = request.form['phone']; district = request.form['district']
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        conn = get_db_connection(); cur = conn.cursor()
        try:
            cur.execute('INSERT INTO users (name, email, password_hash, phone_number, water_district) VALUES (%s, %s, %s, %s, %s) RETURNING id;', (name, email, hashed_password, phone, district))
            new_user_id = cur.fetchone()[0]
            conn.commit()
        except psycopg2.IntegrityError:
            flash('Email address already registered.', 'error'); cur.close(); conn.close(); return redirect(url_for('home') + '#signup')
        cur.close(); conn.close()
        session['user_id'] = new_user_id
        subject = "Welcome to Agri-Water Marketplace!"; html_content = f"<strong>Hi {name},</strong><p>Thank you for joining AWM. You can now log in to get full access.</p>"
        send_email(email, subject, html_content)
        flash('Account created! Please log in to access the marketplace.', 'success')
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']; password = request.form['password']
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT * FROM users WHERE email = %s', (email,))
    user = cur.fetchone()
    cur.close(); conn.close()
    if user and bcrypt.check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']; session['user_name'] = user['name']
        session['subscription_status'] = 'active'
        session['stripe_account_id'] = user['stripe_account_id']
        flash('Logged in successfully!', 'success'); return redirect(url_for('marketplace'))
    else:
        flash('Login failed. Check your email and password.', 'error'); return redirect(url_for('home') + '#signin')

@app.route('/logout')
def logout():
    session.clear(); flash('You have been logged out.', 'success'); return redirect(url_for('home'))

@app.route('/connect_stripe')
def connect_stripe():
    if 'user_id' not in session: return redirect(url_for('home') + '#signin')
    return render_template('connect_stripe.html')

@app.route('/stripe/authorize')
def stripe_authorize():
    if 'user_id' not in session: return redirect(url_for('home') + '#signin')
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT * FROM users WHERE id = %s', (session['user_id'],))
    user = cur.fetchone()
    account_id = user['stripe_account_id']
    if not account_id:
        try:
            account = stripe.Account.create(type='standard', email=user['email'])
            account_id = account.id
            cur.execute('UPDATE users SET stripe_account_id = %s WHERE id = %s', (account_id, session['user_id'])); conn.commit()
            session['stripe_account_id'] = account_id
        except Exception as e:
            flash(f'Could not create a Stripe account: {e}', 'error'); cur.close(); conn.close(); return redirect(url_for('dashboard'))
    cur.close(); conn.close()
    try:
        account_link = stripe.AccountLink.create(account=account_id, refresh_url=YOUR_DOMAIN + '/stripe/authorize', return_url=YOUR_DOMAIN + '/stripe/return', type='account_onboarding')
        return redirect(account_link.url, code=302)
    except Exception as e:
        flash(f'Could not connect to Stripe: {e}', 'error'); return redirect(url_for('dashboard'))

@app.route('/stripe/return')
def stripe_return():
    flash('Your account has been connected to Stripe successfully!', 'success'); return redirect(url_for('dashboard'))

@app.route('/marketplace')
def marketplace():
    if 'user_id' not in session:
        flash('You must be logged in to access the marketplace.', 'error')
        return redirect(url_for('home') + '#signin')
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT l.*, u.name as seller_name, u.allocation_status FROM listings l JOIN users u ON l.seller_id = u.id WHERE l.status = %s', ("active",))
    listings = cur.fetchall()
    cur.close(); conn.close()
    return render_template('marketplace.html', listings=listings)

@app.route('/create_listing', methods=['GET', 'POST'])
def create_listing():
    if 'user_id' not in session:
        flash('You must be logged in to create a listing.', 'error')
        return redirect(url_for('home') + '#signin')
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT stripe_account_id FROM users WHERE id = %s', (session['user_id'],))
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user['stripe_account_id']:
        flash('You must connect a Stripe account before you can create listings.', 'error'); return redirect(url_for('connect_stripe'))

    if request.method == 'POST':
        listing_type = "Lease"
        lease_duration = request.form['lease_duration']
        water_district = request.form['water_district']
        amount_af = request.form['amount_af']
        price_per_af = request.form['price_per_af']
        description = request.form.get('description', '')
        seller_id = session['user_id']
        
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute(
            'INSERT INTO listings (seller_id, listing_type, lease_duration, water_district, amount_af, price_per_af, description) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (seller_id, listing_type, lease_duration, water_district, amount_af, price_per_af, description)
        ); 
        conn.commit()
        cur.close(); conn.close()
        flash('Your lease listing has been created successfully!', 'success'); return redirect(url_for('dashboard'))
    return render_template('create_listing.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('home') + '#signin')
    user_id = session['user_id']
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT * FROM users WHERE id = %s', (user_id,))
    current_user = cur.fetchone()
    cur.execute('SELECT * FROM listings WHERE seller_id = %s ORDER BY created_at DESC', (user_id,))
    my_listings = cur.fetchall()
    
    stripe_account_is_active = False
    if current_user and current_user['stripe_account_id']:
        try:
            stripe_account = stripe.Account.retrieve(current_user['stripe_account_id'])
            if stripe_account.charges_enabled:
                stripe_account_is_active = True
        except Exception as e:
            print(f"Could not retrieve Stripe account: {e}")
            stripe_account_is_active = False

    cur.execute('SELECT SUM(amount_af) as total FROM listings WHERE seller_id = %s AND status = %s', (user_id, 'sold'))
    listings_sold_query = cur.fetchone()
    total_sold = listings_sold_query['total'] if listings_sold_query['total'] is not None else 0
    
    cur.execute(
        'SELECT SUM(l.amount_af) as total FROM listings l '
        'WHERE l.status = %s AND EXISTS (SELECT 1 FROM offers WHERE offers.listing_id = l.id AND offers.buyer_id = %s AND offers.status = %s)',
        ('sold', user_id, 'accepted')
    )
    listings_purchased_query = cur.fetchone()
    total_purchased = listings_purchased_query['total'] if listings_purchased_query['total'] is not None else 0

    current_balance = (current_user['annual_allocation'] or 0) - total_sold + total_purchased
    
    success_lake_info = get_success_lake_data()
    
    cur.close(); conn.close()
    return render_template('dashboard.html', 
                           my_listings=my_listings, 
                           current_user=current_user, 
                           current_balance=current_balance, 
                           stripe_account_is_active=stripe_account_is_active,
                           success_lake_info=success_lake_info)

@app.route('/purchase/<int:listing_id>')
def purchase(listing_id):
    if 'user_id' not in session: flash('You must be logged in to purchase.', 'error'); return redirect(url_for('home') + '#signin')
    buyer_id = session['user_id']
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT * FROM listings WHERE id = %s', (listing_id,))
    listing = cur.fetchone()
    if listing and listing['seller_id'] == buyer_id:
        flash("You cannot purchase your own listing.", 'error'); cur.close(); conn.close(); return redirect(url_for('marketplace'))
    
    cur.execute('SELECT * FROM users WHERE id = %s', (listing['seller_id'],))
    seller = cur.fetchone()
    if not seller['stripe_account_id']:
        flash('This seller has not yet set up to receive payments.', 'error'); cur.close(); conn.close(); return redirect(url_for('marketplace'))

    try:
        stripe_account = stripe.Account.retrieve(seller['stripe_account_id'])
        if not stripe_account.charges_enabled:
            flash('This seller has not completed their Stripe setup and cannot receive payments yet. Please try again later.', 'error')
            cur.close(); conn.close(); return redirect(url_for('marketplace'))
    except Exception as e:
        flash(f'There was an issue verifying the seller\'s account: {e}', 'error'); cur.close(); conn.close(); return redirect(url_for('marketplace'))

    try:
        total_price = int(float(listing['price_per_af']) * float(listing['amount_af']) * 100)
        application_fee = int(total_price * 0.035)
        
        checkout_session = stripe.checkout.Session.create(
            line_items=[{'price_data': {'currency': 'usd', 'product_data': {'name': f"{listing['amount_af']} AF of water from {seller['name']}"}, 'unit_amount': total_price}, 'quantity': 1}],
            mode='payment',
            payment_intent_data={'application_fee_amount': application_fee, 'transfer_data': {'destination': seller['stripe_account_id']}},
            success_url=YOUR_DOMAIN + f'/purchase_success/{listing_id}/{buyer_id}',
            cancel_url=YOUR_DOMAIN + '/cancel',
        )
        cur.close(); conn.close()
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        flash(f'Could not process payment: {e}', 'error'); cur.close(); conn.close(); return redirect(url_for('marketplace'))

@app.route('/purchase_success/<int:listing_id>/<int:buyer_id>')
def purchase_success(listing_id, buyer_id):
    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT * FROM listings WHERE id = %s', (listing_id,))
    listing = cur.fetchone()
    cur.execute('SELECT * FROM users WHERE id = %s', (listing['seller_id'],))
    seller = cur.fetchone()
    cur.execute('SELECT * FROM users WHERE id = %s', (buyer_id,))
    buyer = cur.fetchone()
    
    cur.execute("UPDATE listings SET status = 'sold' WHERE id = %s", (listing_id,))
    cur.execute('INSERT INTO offers (listing_id, buyer_id, status) VALUES (%s, %s, %s)', (listing_id, buyer_id, 'accepted'))
    conn.commit()
    
    pdf_filename = create_lease_agreement(listing, seller, buyer)
    send_for_signature(pdf_filename, seller, buyer)

    if seller:
        send_email(seller['email'], "You've been paid!", f"Congratulations, your listing for {listing['amount_af']} AF was purchased by {buyer['name']}. The lease agreement has been sent to both parties for signature.")
    if buyer:
        send_email(buyer['email'], "Purchase Successful!", f"Congratulations, you have successfully purchased the water listing from {seller['name']}. A lease agreement has been sent to you for signature.")
    
    cur.close(); conn.close()
    flash('Purchase successful! The lease agreement has been sent to both parties for signature.', 'success')
    return redirect(url_for('marketplace'))

@app.route('/set_allocation', methods=['POST'])
def set_allocation():
    if 'user_id' not in session:
        return redirect(url_for('home') + '#signin')
    
    allocation = request.form['annual_allocation']; user_id = session['user_id']
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute('UPDATE users SET annual_allocation = %s WHERE id = %s', (allocation, user_id))
    conn.commit()
    cur.close(); conn.close()
    flash('Your annual allocation has been updated.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/upload_verification', methods=['POST'])
def upload_verification():
    if 'user_id' not in session:
        return redirect(url_for('home') + '#signin')
    if 'verification_doc' not in request.files:
        flash('No file part', 'error'); return redirect(url_for('dashboard'))
    file = request.files['verification_doc']
    if file.filename == '':
        flash('No selected file', 'error'); return redirect(url_for('dashboard'))
    if file and allowed_file(file.filename):
        flash('Verification document uploaded. This feature is under development.', 'success')
        return redirect(url_for('dashboard'))
    else:
        flash('File type not allowed.', 'error'); return redirect(url_for('dashboard'))

@app.route('/edit_listing/<int:listing_id>', methods=['GET', 'POST'])
def edit_listing(listing_id):
    if 'user_id' not in session:
        return redirect(url_for('home') + '#signin')

    conn = get_db_connection(); cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute('SELECT * FROM listings WHERE id = %s AND seller_id = %s', (listing_id, session['user_id']))
    listing = cur.fetchone()

    if not listing:
        flash('Listing not found or you do not have permission to edit it.', 'error')
        cur.close(); conn.close()
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        lease_duration = request.form['lease_duration']
        water_district = request.form['water_district']
        amount_af = request.form['amount_af']
        price_per_af = request.form['price_per_af']
        description = request.form.get('description', '')

        cur.execute(
            'UPDATE listings SET lease_duration = %s, water_district = %s, amount_af = %s, price_per_af = %s, description = %s WHERE id = %s',
            (lease_duration, water_district, amount_af, price_per_af, description, listing_id)
        )
        conn.commit()
        cur.close(); conn.close()
        flash('Your listing has been updated successfully!', 'success')
        return redirect(url_for('dashboard'))

    cur.close(); conn.close()
    return render_template('edit_listing.html', listing=listing)

@app.route('/delete_listing/<int:listing_id>', methods=['POST'])
def delete_listing(listing_id):
    if 'user_id' not in session:
        return redirect(url_for('home') + '#signin')

    conn = get_db_connection(); cur = conn.cursor()
    cur.execute('DELETE FROM listings WHERE id = %s AND seller_id = %s', (listing_id, session['user_id']))
    conn.commit()
    cur.close(); conn.close()
    
    flash('Your listing has been deleted.', 'success')
    return redirect(url_for('dashboard'))

@app.route('/how-it-works')
def how_it_works():
    return render_template('how_it_works.html')

@app.route('/debug-env')
def debug_env():
    db_url = os.environ.get('DATABASE_URL')
    if db_url:
        parts = db_url.split('@')
        hidden_url = parts[0].split(':')[0] + ':PASSWORD_HIDDEN@' + parts[1]
        return f"SUCCESS: The DATABASE_URL is set. It looks like this: {hidden_url}"
    else:
        return "ERROR: The DATABASE_URL is NOT SET. The value is None."