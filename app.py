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

# app.py

def refresh_user_session(user_id):
    """
    DB에서 최신 회원 정보를 조회하여 세션(Session) 정보를 동기화하는 함수
    돈(Points)이나 상태(Status)가 변경된 직후에 호출하면 무결성이 보장됩니다.
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT name, points, status, is_manager FROM Residents WHERE resident_id = %s", (user_id,))
        user = cur.fetchone()
        if user:
            # DB의 최신 값을 세션에 덮어씌움 (확실한 동기화)
            session['name'] = user[0]
            session['points'] = user[1]
            session['status'] = user[2]
            session['is_manager'] = user[3]
    except Exception as e:
        print(f"Session refresh failed: {e}")
    finally:
        cur.close()
        conn.close()

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
    # [추가] 0. 접속 시 자동 연체 처리 (Lazy Update)
    # 반납일(end_date)이 어제보다 과거이고, 상태가 아직 'rented'인 경우 -> 'overdue'로 변경
    # ======================================================
    cur.execute("""
        UPDATE Rentals 
        SET status = 'overdue' 
        WHERE status = 'rented' AND end_date < CURRENT_DATE
    """)
    conn.commit()

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
    arrived_returns = []  # [추가 1] 변수 초기화
    owner_history = []

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

        # ==========================================================
        # [추가 2] 여기에 반납 도착 확인 쿼리를 넣으세요!
        # ==========================================================
        # [소유자] 반납 도착 확인 대기
        cur.execute("""
            SELECT r.rental_id, i.name, u.name, 
                   p.name, p.phone_number -- [추가] 기사 정보
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u ON r.borrower_id = u.resident_id
            LEFT JOIN View_Manager_Residents p ON r.delivery_partner_id = p.resident_id
            WHERE i.owner_id = %s AND r.delivery_status = 'arrived'
        """, (session['resident_id'],))
        arrived_returns = cur.fetchall()

        # [수정] 내 물건의 지난 대여 이력 조회
        # 조건: 상태가 'returned'(반납확정) 또는 'disputed'(분쟁중) 인 것만 조회
        cur.execute("""
            SELECT r.rental_id, i.name, u.name, r.start_date, r.end_date, r.status, 
                   (r.end_date - r.start_date + 1) * i.rent_fee as total_income
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u ON r.borrower_id = u.resident_id
            WHERE i.owner_id = %s 
              AND r.status IN ('returned', 'disputed')  -- ['completed' 삭제함]
            ORDER BY r.rental_id DESC
        """, (session['resident_id'],))
        owner_history = cur.fetchall()

    # render_template에 owner_history=owner_history 추가 필수!

    # 3. [대여자] 탭 데이터 조회
    my_rentals = []
    if session.get('status') == 'approved':
        # [수정 1] Residents -> View_Manager_Residents 로 변경
        cur.execute("""
            SELECT r.rental_id, i.name, u.name, r.start_date, r.end_date, r.status, 
                   r.delivery_status, 
                   p.name, p.phone_number  -- [추가] 기사 이름, 기사 폰번호
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u ON i.owner_id = u.resident_id 
            LEFT JOIN View_Manager_Residents p ON r.delivery_partner_id = p.resident_id -- 기사 조인
            WHERE r.borrower_id = %s ORDER BY r.rental_id DESC
        """, (session['resident_id'],))
        my_rentals = cur.fetchall()

# 4. [배송] 탭 로직
    delivery_market = []
    my_deliveries = []
    if session.get('status') == 'approved':
        # [수정] WHERE 절 마지막에 AND r.borrower_id != %s 추가
        # 의미: 내가 빌린 건(Borrower가 나인 건)은 배송 시장 리스트에서 제외
        cur.execute("""
            SELECT r.rental_id, i.name, r.delivery_fee, 
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u2.building ELSE u1.building END,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u2.unit ELSE u1.unit END,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u1.building ELSE u2.building END,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u1.unit ELSE u2.unit END,
                   r.status
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u1 ON i.owner_id = u1.resident_id 
            JOIN View_Manager_Residents u2 ON r.borrower_id = u2.resident_id
            WHERE 
                (
                    (r.status = 'approved' AND r.delivery_option = 'delivery' AND r.delivery_partner_id IS NULL)
                    OR 
                    (r.status IN ('rented', 'overdue') AND r.delivery_status = 'waiting_driver')
                )
                AND r.borrower_id != %s  -- [핵심] 내 요청은 안 보이게 처리
        """, (session['resident_id'],))
        delivery_market = cur.fetchall()


        # 내 배송 현황도 동일하게 적용
        # [배송] 내 배송 현황 (기사 입장에서 보는 뷰)
        cur.execute("""
            SELECT r.rental_id, i.name, r.delivery_fee, 
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u2.building ELSE u1.building END,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u2.unit ELSE u1.unit END,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u1.building ELSE u2.building END,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u1.unit ELSE u2.unit END,
                   r.delivery_status, r.status,
                   -- [추가] 출발지/목적지 전화번호 로직
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u2.phone_number ELSE u1.phone_number END as start_phone,
                   CASE WHEN r.status IN ('rented', 'overdue') THEN u1.phone_number ELSE u2.phone_number END as end_phone
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            JOIN View_Manager_Residents u1 ON i.owner_id = u1.resident_id 
            JOIN View_Manager_Residents u2 ON r.borrower_id = u2.resident_id
            WHERE r.delivery_partner_id = %s AND r.delivery_status != 'completed'
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
                           arrived_returns=arrived_returns,
                           owner_history=owner_history,  # <--- [★중요★] 이 줄이 꼭 있어야 이력이 뜹니다!
                           my_rentals=my_rentals,
                           delivery_market=delivery_market,
                           my_deliveries=my_deliveries,
                           pending_residents=pending_residents,
                           open_disputes=open_disputes,
                           history_residents=history_residents,
                           search_query=search_query,
                           filter_status=filter_status,
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
        #start_date = request.form['start_date']      
        start_date_obj = date.today()          # 날짜 객체 (DB 저장용)
        end_date_str = request.form['end_date'] # 문자열 (폼 입력값)

        # 날짜 계산을 위해 형변환
        d1 = datetime.combine(start_date_obj, datetime.min.time()) # date -> datetime 변환
        d2 = datetime.strptime(end_date_str, "%Y-%m-%d")
        
        days = (d2 - d1).days + 1
        
        if days < 1:
             flash("❌ 반납일은 오늘 이후여야 합니다.", "danger")
             return redirect(url_for('rent_item', item_id=item_id))

        delivery_option = request.form['delivery_option']
        del_fee = 500 if delivery_option == 'delivery' else 0
        total_cost = (days * item[5]) + del_fee

        try:
            cur.execute("""
                INSERT INTO Rentals (item_id, borrower_id, start_date, end_date, delivery_option, delivery_fee)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (item_id, session['resident_id'], start_date_obj, end_date_str, delivery_option, del_fee))
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
# app.py

@app.route('/approve_rental/<int:rental_id>')
def approve_rental(rental_id):
    if session.get('status') != 'approved': return "권한 없음"

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        # 1. 정보 조회
        cur.execute("""
            SELECT r.borrower_id, i.owner_id, i.rent_fee, r.start_date, r.end_date, r.delivery_fee, r.item_id
            FROM Rentals r JOIN Items i ON r.item_id = i.item_id 
            WHERE r.rental_id = %s
        """, (rental_id,))
        data = cur.fetchone()
        
        borrower, owner, fee_per_day, s_date, e_date, del_fee, item_id = data
        
        days = (e_date - s_date).days + 1
        total = (days * fee_per_day) + del_fee

        # 2. 포인트 정산 (트랜잭션)
        cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (total, borrower))
        cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (total, owner))
        
        # 3. 대여 상태 승인 처리
        cur.execute("UPDATE Rentals SET status = 'approved' WHERE rental_id = %s", (rental_id,))
        
        # 4. 물품 상태 변경 (목록에서 숨김)
        cur.execute("UPDATE Items SET status = 'rented' WHERE item_id = %s", (item_id,))
        
        # ==========================================================
        # [수정된 부분] 배송 옵션에 따른 상태 분기 처리
        # ==========================================================
        if del_fee > 0:
            # (A) 배송 대행: 기사 매칭 대기 상태로 설정
            cur.execute("UPDATE Rentals SET delivery_status = 'waiting_driver' WHERE rental_id = %s", (rental_id,))
        else:
            # (B) 직거래(Pickup): 대여자 본인을 배송 기사로 자동 지정 (Self-Delivery)
            # 배송비는 0원이지만, 상태 관리를 위해 '내 배송 현황'에 등록됨
            cur.execute("""
                UPDATE Rentals 
                SET delivery_partner_id = %s, delivery_status = 'accepted' 
                WHERE rental_id = %s
            """, (borrower, rental_id))
        
        conn.commit()
        refresh_user_session(session['resident_id'])
    
        flash(f"✅ 승인 완료! {total}P 정산됨.", "success")

    except Exception as e:
        conn.rollback()
        flash(f"❌ 승인 실패: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('index'))
# ==========================================
# 대여 거절
# ==========================================
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

# 2. 배송 취소 라우트 추가 (app.py 맨 아래쪽이나 accept_delivery 근처)
# ---------------------------------------------------------
@app.route('/cancel_delivery/<int:rental_id>')
def cancel_delivery(rental_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. 현재 배송 정보와 관련자(Borrower, Owner) 정보 조회
        cur.execute("""
            SELECT r.delivery_fee, r.borrower_id, i.owner_id, r.delivery_partner_id, r.delivery_status
            FROM Rentals r JOIN Items i ON r.item_id = i.item_id 
            WHERE r.rental_id = %s
        """, (rental_id,))
        result = cur.fetchone()

        if not result: return "잘못된 접근"
        
        fee, borrower_id, owner_id, partner_id, status = result
        
        # 권한 체크: 내 배송이 맞는지, 그리고 취소 가능한 상태(accepted)인지
        if partner_id != session['resident_id'] or status != 'accepted':
            flash("❌ 취소할 수 없는 상태입니다.", "danger")
            return redirect(url_for('index'))

        # ==========================================================
        # [핵심 로직] 직거래(0원) 취소 시 -> 배송 대행(500원)으로 전환
        # ==========================================================
        if fee == 0:
            # (1) 잔액 확인
            cur.execute("SELECT points FROM Residents WHERE resident_id = %s", (session['resident_id'],))
            my_points = cur.fetchone()[0]
            
            if my_points < 500:
                flash("❌ 직거래를 취소하고 배송 대행을 맡기려면 500P가 필요합니다. (잔액 부족)", "danger")
                return redirect(url_for('index'))
            
            # (2) 포인트 결제 (나 -> 소유자 에스크로)
            cur.execute("UPDATE Residents SET points = points - 500 WHERE resident_id = %s", (session['resident_id'],))
            cur.execute("UPDATE Residents SET points = points + 500 WHERE resident_id = %s", (owner_id,))
            
            # (3) 렌탈 정보 업데이트 (배송비 0 -> 500, 옵션 변경)
            # 직거래를 포기했으니 이제 이 건은 '배송 대행' 건이 됩니다.
            cur.execute("""
                UPDATE Rentals 
                SET delivery_partner_id = NULL, 
                    delivery_status = 'waiting_driver',
                    delivery_fee = 500,
                    delivery_option = 'delivery'
                WHERE rental_id = %s
            """, (rental_id,))
            
            flash("✅ 직거래를 취소했습니다. 500P가 결제되었으며 배송 기사를 기다립니다.", "info")

        # ==========================================================
        # [일반 로직] 원래 배송 대행(500원)이었던 건을 알바가 취소
        # ==========================================================
        else:
            # 돈은 이미 소유자에게 있으므로 상태만 리셋하면 됨
            cur.execute("""
                UPDATE Rentals 
                SET delivery_partner_id = NULL, delivery_status = 'waiting_driver'
                WHERE rental_id = %s
            """, (rental_id,))
            
            flash("bucket 배송 업무를 취소했습니다. 해당 건은 다시 대기 목록으로 이동합니다.", "warning")
        
        conn.commit()
        # [수정] 500P를 썼거나, 변동이 있었으니 확실하게 동기화
        refresh_user_session(session['resident_id'])
    except Exception as e:
        conn.rollback()
        print(e)
        flash(f"오류: {e}", "danger")
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('index'))
# app.py
# ==========================================
# 배송기사 배송 완료
# ==========================================
@app.route('/complete_delivery/<int:rental_id>')
def complete_delivery(rental_id):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 상태 확인
        cur.execute("SELECT status FROM Rentals WHERE rental_id = %s", (rental_id,))
        status = cur.fetchone()[0]

        # [수정] 반납 과정(rented/overdue)인 경우 -> 'arrived' 상태로 변경 (소유자 확인 대기)
        if status in ['rented', 'overdue']:
            cur.execute("UPDATE Rentals SET delivery_status = 'arrived' WHERE rental_id = %s", (rental_id,))
            flash("🚚 목적지에 도착했습니다! 소유자의 확인을 기다리세요.", "info")
        
        # [기존] 대여 과정(approved)인 경우 -> 'rented' 상태로 변경 (대여 시작)
        else:
            # ... (기존 포인트 지급 로직 유지) ...
            cur.execute("SELECT delivery_fee, borrower_id, i.owner_id FROM Rentals r JOIN Items i ON r.item_id = i.item_id WHERE rental_id = %s", (rental_id,))
            fee, borrower, owner = cur.fetchone()
            
            if fee > 0:
                cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (fee, owner))
                cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (fee, session['resident_id']))
                flash(f"✅ 배송 완료! 수고비 {fee} 포인트를 받았습니다.", "success")
            
            cur.execute("UPDATE Rentals SET delivery_status = 'completed', status = 'rented' WHERE rental_id = %s", (rental_id,))
        
        conn.commit()

        # [수정] 내(배송기사) 포인트가 변했으므로 동기화
        refresh_user_session(session['resident_id'])
    except Exception as e:
        conn.rollback()
        flash(f"오류: {e}", "danger")
    finally:
        cur.close()
        conn.close()
    return redirect(url_for('index'))
# app.py 에 추가
# ==========================================
# 반납 배송
# ==========================================
@app.route('/request_return/<int:rental_id>', methods=['POST'])
def request_return(rental_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    option = request.form['delivery_option'] # 'pickup' or 'delivery'
    fee = 500 if option == 'delivery' else 0
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. 현재 상태 및 잔액 확인
        cur.execute("""
            SELECT r.borrower_id, i.owner_id, r.status 
            FROM Rentals r JOIN Items i ON r.item_id = i.item_id 
            WHERE r.rental_id = %s
        """, (rental_id,))
        rental = cur.fetchone()
        
        borrower_id, owner_id, status = rental
        
        # 이미 반납된 상태면 중단
        if status not in ['rented', 'overdue']:
            flash("❌ 이미 반납되었거나 반납할 수 없는 상태입니다.", "warning")
            return redirect(url_for('index'))

        # 2. 배송비 트랜잭션 (배송 반납인 경우)
        if fee > 0:
            cur.execute("SELECT points FROM Residents WHERE resident_id = %s", (borrower_id,))
            current_points = cur.fetchone()[0]
            
            if current_points < fee:
                flash("❌ 잔액이 부족하여 배송 반납을 신청할 수 없습니다.", "danger")
                return redirect(url_for('index'))
                
            # Borrower 차감 -> Owner에게 임시 지급 (배송 완료 시 기사에게 이동)
            cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (fee, borrower_id))
            cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (fee, owner_id))

        # 3. [핵심] 기존 배송 정보 덮어쓰기 (Return 모드로 전환)
        # delivery_status를 초기화하여 새로운 운송 사이클 시작
        
        new_delivery_status = 'waiting_driver' if option == 'delivery' else 'accepted'
        partner_id = None if option == 'delivery' else borrower_id # 직접 반납이면 본인이 파트너

        cur.execute("""
            UPDATE Rentals 
            SET delivery_option = %s,
                delivery_fee = %s,
                delivery_partner_id = %s,
                delivery_status = %s
            WHERE rental_id = %s
        """, (option, fee, partner_id, new_delivery_status, rental_id))
        
        conn.commit()
        flash("↩️ 반납 신청이 접수되었습니다. 운송 절차를 진행해주세요.", "success")
        
    except Exception as e:
        conn.rollback()
        print(e)
        flash("오류 발생", "danger")
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('index'))
# ==========================================
# 소유자 반납확인
# ==========================================
# app.py

@app.route('/confirm_return/<int:rental_id>')
def confirm_return(rental_id):
    if 'user_id' not in session: return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. 필요한 모든 정보 조회 (날짜, 요금, 당사자들)
        cur.execute("""
            SELECT r.delivery_fee, r.delivery_partner_id, r.item_id, i.owner_id,
                   r.start_date, r.end_date, r.borrower_id, i.rent_fee
            FROM Rentals r 
            JOIN Items i ON r.item_id = i.item_id 
            WHERE r.rental_id = %s
        """, (rental_id,))
        data = cur.fetchone()
        
        if not data: return "데이터 없음"
        
        # 변수 할당
        del_fee, partner_id, item_id, owner_id, start_date, original_end_date, borrower_id, rent_fee = data

        # 권한 체크
        if session['resident_id'] != owner_id:
            flash("권한이 없습니다.", "danger")
            return redirect(url_for('index'))

        # ---------------------------------------------------------
        # [신규 기능] 조기 반납 시 차액 환불 & 날짜 보정 로직
        # ---------------------------------------------------------
        today = date.today()
        
        # 남은 기간 계산 (예: 5일 반납인데 3일에 옴 -> 2일치 환불)
        # 단, 시작일보다 이전(미래 예약 취소 등)인 경우는 별도 처리가 필요하지만 
        # 여기선 '대여 중'인 상태이므로 start_date <= today 라고 가정함.
        remaining_days = (original_end_date - today).days
        
        refund_msg = ""

        # 남은 날짜가 하루 이상이면 환불 진행
        if remaining_days > 0:
            refund_amount = remaining_days * rent_fee
            
            # (1) 환불 트랜잭션 (소유자 -> 대여자)
            if refund_amount > 0:
                cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (refund_amount, owner_id))
                cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (refund_amount, borrower_id))
            
            # (2) 종료일 업데이트 (오늘로 수정)
            cur.execute("UPDATE Rentals SET end_date = %s WHERE rental_id = %s", (today, rental_id))
            
            refund_msg = f" (⚡ 조기 반납으로 {remaining_days}일치 {refund_amount}P가 환불되었습니다!)"

        # ---------------------------------------------------------
        # [기존 기능] 배송비 정산 (소유자 -> 배송기사)
        # ---------------------------------------------------------
        if del_fee > 0 and partner_id:
            cur.execute("UPDATE Residents SET points = points - %s WHERE resident_id = %s", (del_fee, owner_id))
            cur.execute("UPDATE Residents SET points = points + %s WHERE resident_id = %s", (del_fee, partner_id))

        # 3. 상태 업데이트 (최종 완료)
        cur.execute("UPDATE Rentals SET status = 'returned', delivery_status = 'completed' WHERE rental_id = %s", (rental_id,))
        
        # 4. 물품 상태 복구
        cur.execute("UPDATE Items SET status = 'available' WHERE item_id = %s", (item_id,))
        
        conn.commit()
        
        # 세션 동기화 (내 포인트가 빠져나갔을 수 있으므로)
        refresh_user_session(session['resident_id'])
        
        flash(f"✅ 반납 확인 완료!{refund_msg}", "success")
        
    except Exception as e:
        conn.rollback()
        print("에러:", e)
        flash(f"오류 발생: {e}", "danger")
    finally:
        cur.close()
        conn.close()
        
    return redirect(url_for('index'))

# ==========================================
# 분쟁신고
# ==========================================
@app.route('/report_dispute/<int:rental_id>', methods=['POST'])
def report_dispute(rental_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    
    reason = request.form['reason'] # 모달에서 입력한 사유
    
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. 정보 조회
        cur.execute("""
            SELECT r.item_id, i.owner_id 
            FROM Rentals r JOIN Items i ON r.item_id = i.item_id 
            WHERE r.rental_id = %s
        """, (rental_id,))
        result = cur.fetchone()
        
        if not result: return "잘못된 요청"
        item_id, owner_id = result
        
        # 권한 체크
        if owner_id != session['resident_id']:
            flash("권한이 없습니다.", "danger")
            return redirect(url_for('index'))

        # 2. 상태 변경 (Lock)
        # Rentals -> disputed, Items -> disputed
        cur.execute("UPDATE Rentals SET status = 'disputed' WHERE rental_id = %s", (rental_id,))
        cur.execute("UPDATE Items SET status = 'disputed' WHERE item_id = %s", (item_id,))
        
        # 3. 분쟁 테이블에 기록 (Disputes)
        cur.execute("""
            INSERT INTO Disputes (rental_id, reason, status)
            VALUES (%s, %s, 'open')
        """, (rental_id, reason))
        
        conn.commit()
        flash("🚨 분쟁 신고가 접수되었습니다. 관리자 판결 전까지 물품이 잠금 처리됩니다.", "warning")
        
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