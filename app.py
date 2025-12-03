from flask import Flask, render_template, request, redirect, url_for, session, flash
import psycopg2
from psycopg2 import errors
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, datetime

app = Flask(__name__)
app.secret_key = 'super_secret_key'  # 실제 배포시엔 복잡한 값 사용

# ==========================================
# 1. DB 접속 정보 (이원화 전략)
# ==========================================
MANAGER_CONF = {
    'host': 'localhost', 'dbname': 'DB_Term_Project', 'port': '5432',
    'user': 'db_manager', 'password': 'manager1234' 
}

RESIDENT_CONF = {
    'host': 'localhost', 'dbname': 'DB_Term_Project', 'port': '5432',
    'user': 'db_resident', 'password': 'resident1234'
}

def get_db_connection():
    # 매니저 권한이 세션에 있으면 매니저 계정으로 접속
    if session.get('is_manager'):
        return psycopg2.connect(**MANAGER_CONF)
    else:
        # 일반 유저나 비로그인 상태면 주민 계정으로 접속
        return psycopg2.connect(**RESIDENT_CONF)

# ==========================================
# 2. 메인 대시보드 (데이터 조회)
# ==========================================
# app.py 의 index 함수 전체 교체
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    # ======================================================
    # [수정] 1. 검색/필터 기능이 적용된 물품 목록 조회
    # ======================================================
    
    # URL 파라미터 받기 (예: /?keyword=드릴&category=공구/수리&sort=date)
    keyword = request.args.get('keyword', '').strip()
    category_filter = request.args.get('category', '')
    sort_option = request.args.get('sort', 'latest')  # 기본값: 최신순

    # 기본 쿼리: 대여 가능하고 만료되지 않은 물품
    query = """
        SELECT item_id, name, category, rent_fee, expiration_date, description, owner_id 
        FROM Items 
        WHERE status = 'available' AND expiration_date >= CURRENT_DATE
    """
    params = []

    # (1) 텍스트 검색 (상품명 또는 설명에 포함)
    if keyword:
        query += " AND (name ILIKE %s OR description ILIKE %s)"
        params.extend([f'%{keyword}%', f'%{keyword}%'])
    
    # (2) 카테고리 필터
    if category_filter:
        query += " AND category = %s"
        params.append(category_filter)

    # (3) 정렬 (빠른 만료일순 vs 최신 등록순)
    if sort_option == 'exp_date':
        query += " ORDER BY expiration_date ASC, item_id DESC" # 만료일 임박한 순
    else:
        query += " ORDER BY item_id DESC" # 최신 등록순 (기본)

    cur.execute(query, tuple(params))
    items = cur.fetchall()

    # 2. [소유자]
    my_items = []
    incoming_requests = []
    # [수정됨] is_verified 대신 status가 'approved'인지 확인
    if session.get('status') == 'approved': 
        cur.execute("SELECT * FROM Items WHERE owner_id = %s", (session['resident_id'],))
        my_items = cur.fetchall()
        cur.execute("""
            SELECT r.rental_id, i.name, u.name, r.start_date, r.end_date, r.status
            FROM Rentals r JOIN Items i ON r.item_id = i.item_id JOIN View_Manager_Residents u ON r.borrower_id = u.resident_id
            WHERE i.owner_id = %s AND r.status = 'requested'
        """, (session['resident_id'],))
        incoming_requests = cur.fetchall()

    # 3. [대여자] 탭 데이터 조회
    my_rentals = []
    if session.get('status') == 'approved':
        # [수정 1] Residents -> View_Manager_Residents 로 변경
        cur.execute("""
            SELECT r.rental_id, i.name, u.name, r.start_date, r.end_date, r.status, r.delivery_status
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u ON i.owner_id = u.resident_id  -- 여기를 수정
            WHERE r.borrower_id = %s ORDER BY r.rental_id DESC
        """, (session['resident_id'],))
        my_rentals = cur.fetchall()

    # 4. [배송] 탭 데이터 조회
    delivery_market = []
    my_deliveries = []
    if session.get('status') == 'approved':
        # [수정 2] Residents -> View_Manager_Residents 로 변경 (u1, u2 둘 다)
        cur.execute("""
            SELECT r.rental_id, i.name, r.delivery_fee, u1.building, u1.unit, u2.building, u2.unit
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u1 ON i.owner_id = u1.resident_id      -- 여기를 수정
            JOIN View_Manager_Residents u2 ON r.borrower_id = u2.resident_id   -- 여기를 수정
            WHERE r.delivery_option = 'delivery' AND r.status = 'approved' AND r.delivery_partner_id IS NULL
        """)
        delivery_market = cur.fetchall()
        
        # [수정 3] Residents -> View_Manager_Residents 로 변경 (u1, u2 둘 다)
        cur.execute("""
            SELECT r.rental_id, i.name, r.delivery_fee, u1.building, u1.unit, u2.building, u2.unit, r.delivery_status
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u1 ON i.owner_id = u1.resident_id      -- 여기를 수정
            JOIN View_Manager_Residents u2 ON r.borrower_id = u2.resident_id   -- 여기를 수정
            WHERE r.delivery_partner_id = %s AND r.delivery_status != 'delivered'
        """, (session['resident_id'],))
        my_deliveries = cur.fetchall()

    # ---------------------------------------
    # 5. [매니저] 승인 대기 & 분쟁 & [신규] 처리 이력 검색
    # ---------------------------------------
    pending_residents = []
    open_disputes = []
    history_residents = [] # 처리된(승인/거절) 주민 목록
    
    # 검색어(q)와 필터(f) 가져오기 (URL 파라미터)
    search_query = request.args.get('q', '')
    filter_status = request.args.get('f', 'all')

    if session.get('is_manager'):
        # (A) 가입 대기 목록 (Pending)
        cur.execute("""
            SELECT resident_id, user_id, name, phone_number, building, unit 
            FROM View_Manager_Residents 
            WHERE status = 'pending' AND is_manager = FALSE
        """)
        pending_residents = cur.fetchall()
        
        # (B) 분쟁 목록
        cur.execute("""
            SELECT d.dispute_id, r.rental_id, d.reason, u1.name, u2.name
            FROM Disputes d JOIN Rentals r ON d.rental_id = r.rental_id JOIN View_Manager_Residents u1 ON r.borrower_id = u1.resident_id JOIN Items i ON r.item_id = i.item_id JOIN View_Manager_Residents u2 ON i.owner_id = u2.resident_id
            WHERE d.status = 'open'
        """)
        open_disputes = cur.fetchall()

        # (C) [신규] 주민 관리 이력 (History) - 검색 및 필터링 적용
        # 기본 쿼리: 이미 처리된(승인/거절) 주민만 조회
        query = """
            SELECT resident_id, user_id, name, phone_number, building, unit, status 
            FROM View_Manager_Residents 
            WHERE is_manager = FALSE AND status IN ('approved', 'rejected')
        """
        params = []

        # 검색 조건 추가 (아이디 또는 이름)
        if search_query:
            query += " AND (user_id ILIKE %s OR name ILIKE %s)"
            params.extend([f'%{search_query}%', f'%{search_query}%'])
        
        # 필터 조건 추가 (승인됨/거절됨)
        if filter_status == 'approved':
            query += " AND status = 'approved'"
        elif filter_status == 'rejected':
            query += " AND status = 'rejected'"
        
        query += " ORDER BY resident_id DESC" # 최신순 정렬
        
        cur.execute(query, tuple(params))
        history_residents = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('dashboard.html', 
                           items=items,
                           my_items=my_items,
                           incoming_requests=incoming_requests,
                           my_rentals=my_rentals,
                           delivery_market=delivery_market,
                           my_deliveries=my_deliveries,
                           pending_residents=pending_residents,
                           open_disputes=open_disputes,
                           history_residents=history_residents, # [추가]
                           search_query=search_query,           # [추가] 검색어 유지용
                           filter_status=filter_status,         # [추가] 필터 유지용
                           session=session,
                           date_today=date.today())

# ==========================================
# 3. 인증 (회원가입/로그인/로그아웃)
# ==========================================
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        uid = request.form['user_id']
        pw = generate_password_hash(request.form['password'])
        name = request.form['name']
        phone = request.form['phone']
        building = request.form['building']
        unit = request.form['unit']

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # status는 기본값 'pending' 자동 입력됨
            cur.execute("""
                INSERT INTO Residents (user_id, password, name, phone_number, building, unit)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (uid, pw, name, phone, building, unit))
            conn.commit()
            flash("✅ 가입되었습니다. 매니저 승인 후 활동 가능합니다.", "success")
            return redirect(url_for('login'))
        except errors.UniqueViolation as e:
            conn.rollback()
            err_msg = str(e)
            if 'residents_user_id_key' in err_msg:
                flash("❌ 이미 존재하는 아이디입니다.", "danger")
            elif 'residents_phone_number_key' in err_msg:
                flash("❌ 이미 가입된 전화번호입니다.", "danger")
            else:
                flash("❌ 중복된 정보가 있습니다.", "danger")
        except Exception as e:
            conn.rollback()
            flash(f"❌ 오류: {e}", "danger")
        finally:
            cur.close()
            conn.close()
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        uid = request.form['user_id']
        pw = request.form['password']
        
        # [중요] 로그인 검증은 일반 권한(RESIDENT_CONF) 사용
        conn = psycopg2.connect(**RESIDENT_CONF)
        cur = conn.cursor()
        
        # [변경] is_verified 대신 status 컬럼 조회
        cur.execute("""
            SELECT resident_id, password, name, points, status, is_manager 
            FROM Residents WHERE user_id = %s
        """, (uid,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user[1], pw):
            session['resident_id'] = user[0]
            session['user_id'] = uid
            session['name'] = user[2]
            session['points'] = user[3]
            session['status'] = user[4]     # [NEW] status 저장
            session['is_manager'] = user[5]
            return redirect(url_for('index'))
        else:
            flash("❌ 아이디 또는 비밀번호가 틀렸습니다.", "danger")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash("로그아웃 되었습니다.", "info")
    return redirect(url_for('login'))

# ==========================================
# 4. 기능 액션 (물품 등록, 승인 등)
# ==========================================
@app.route('/register_item', methods=['POST'])
def register_item():
    if session.get('status') != 'approved':
        flash("❌ 승인된 주민만 물품을 등록할 수 있습니다.", "warning")
        return redirect(url_for('index'))

    name = request.form['name']
    category = request.form['category']
    desc = request.form['description']
    fee = request.form['rent_fee']
    exp_date = request.form['expiration_date']

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO Items (owner_id, name, category, description, rent_fee, expiration_date)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (session['resident_id'], name, category, desc, fee, exp_date))
        conn.commit()
        flash("📦 물품이 등록되었습니다.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"등록 실패: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('index'))

@app.route('/rent/<int:item_id>', methods=['GET', 'POST'])
def rent_item(item_id):
    if session.get('status') != 'approved':
        flash("❌ 승인된 주민만 대여할 수 있습니다.", "warning")
        return redirect(url_for('index'))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM Items WHERE item_id = %s", (item_id,))
    item = cur.fetchone()

    if item[1] == session['resident_id']:
        flash("🚫 본인의 물건은 대여할 수 없습니다.", "danger")
        return redirect(url_for('index'))
    
    cur.execute("SELECT points FROM Residents WHERE resident_id = %s", (session['resident_id'],))
    my_points = cur.fetchone()[0]

    if request.method == 'POST':
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        delivery_option = request.form['delivery_option']
        
        d1 = datetime.strptime(start_date, "%Y-%m-%d")
        d2 = datetime.strptime(end_date, "%Y-%m-%d")
        days = (d2 - d1).days + 1
        
        del_fee = 500 if delivery_option == 'delivery' else 0
        total_cost = (days * item[5]) + del_fee

        if my_points < total_cost:
            flash(f"❌ 잔액이 부족하여 신청할 수 없습니다. (필요: {total_cost} P)", "danger")
            return redirect(url_for('rent_item', item_id=item_id))

        try:
            cur.execute("""
                INSERT INTO Rentals (item_id, borrower_id, start_date, end_date, delivery_option, delivery_fee)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (item_id, session['resident_id'], start_date, end_date, delivery_option, del_fee))
            conn.commit()
            flash("✅ 대여 신청 완료! 승인을 기다리세요.", "success")
            return redirect(url_for('index'))
        except Exception as e:
            conn.rollback()
            flash(f"신청 실패: {e}", "danger")
        finally:
            cur.close()
            conn.close()

    cur.close()
    conn.close()
    return render_template('rent_form.html', item=item, date_today=date.today(), my_points=my_points)

# [핵심] 대여 승인 (트랜잭션)
@app.route('/approve_rental/<int:rental_id>')
def approve_rental(rental_id):
    if session.get('status') != 'approved': return "권한 없음"

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT r.borrower_id, i.owner_id, i.rent_fee, r.start_date, r.end_date, r.delivery_fee
            FROM Rentals r JOIN Items i ON r.item_id = i.item_id WHERE r.rental_id = %s
        """, (rental_id,))
        data = cur.fetchone()
        
        borrower, owner, fee_per_day, s_date, e_date, del_fee = data
        days = (e_date - s_date).days + 1
        total = (days * fee_per_day) + del_fee

        cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (total, borrower))
        cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (total, owner))
        cur.execute("UPDATE Rentals SET status = 'approved' WHERE rental_id = %s", (rental_id,))
        
        conn.commit()
        flash(f"✅ 승인 완료! {total}P 정산됨.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"❌ 승인 실패: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('index'))

@app.route('/reject_rental/<int:rental_id>')
def reject_rental(rental_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Rentals SET status = 'rejected' WHERE rental_id = %s", (rental_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("요청을 거절했습니다.", "warning")
    return redirect(url_for('index'))

# ==========================================
# 5. 배송 및 관리자 기능
# ==========================================
@app.route('/accept_delivery/<int:rental_id>')
def accept_delivery(rental_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE Rentals 
        SET delivery_partner_id = %s, delivery_status = 'accepted'
        WHERE rental_id = %s
    """, (session['resident_id'], rental_id))
    conn.commit()
    cur.close()
    conn.close()
    flash("🛵 배송을 수락했습니다! 안전하게 배달해주세요.", "success")
    return redirect(url_for('index'))

@app.route('/pickup_delivery/<int:rental_id>')
def pickup_delivery(rental_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Rentals SET delivery_status = 'picked_up' WHERE rental_id = %s", (rental_id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("📦 물품을 픽업했습니다.", "info")
    return redirect(url_for('index'))

@app.route('/complete_delivery/<int:rental_id>')
def complete_delivery(rental_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT delivery_fee, borrower_id FROM Rentals WHERE rental_id = %s", (rental_id,))
        fee, _ = cur.fetchone()
        
        cur.execute("""
            SELECT i.owner_id FROM Rentals r JOIN Items i ON r.item_id = i.item_id WHERE r.rental_id = %s
        """, (rental_id,))
        owner_id = cur.fetchone()[0]
        
        cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (fee, owner_id))
        cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (fee, session['resident_id']))
        
        cur.execute("UPDATE Rentals SET delivery_status = 'delivered' WHERE rental_id = %s", (rental_id,))
        conn.commit()
        flash(f"✅ 배송 완료! 수고비 {fee} 포인트를 받았습니다.", "success")
    except Exception as e:
        conn.rollback()
        flash(f"오류: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('index'))

# ==========================================
# [매니저 액션] 승인 / 거절 / 복구(대기상태로)
# ==========================================
@app.route('/approve_resident/<int:id>')
def approve_resident(id):
    if not session.get('is_manager'): return "권한 없음"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Residents SET status = 'approved' WHERE resident_id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("✅ 승인 처리되었습니다.", "success")
    return redirect(url_for('index'))

@app.route('/reject_resident/<int:id>')
def reject_resident(id):
    if not session.get('is_manager'): return "권한 없음"
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Residents SET status = 'rejected' WHERE resident_id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("🚫 거절(정지) 처리되었습니다.", "warning")
    return redirect(url_for('index'))

@app.route('/restore_resident/<int:id>')
def restore_resident(id):
    if not session.get('is_manager'): return "권한 없음"
    conn = get_db_connection()
    cur = conn.cursor()
    # 상태를 다시 'pending'으로 돌려서 승인 대기 목록으로 보냄
    cur.execute("UPDATE Residents SET status = 'pending' WHERE resident_id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    flash("♻️ 대기 상태로 되돌렸습니다.", "info")
    return redirect(url_for('index'))
if __name__ == '__main__':
    app.run(debug=True)