-- ========================================================
-- [Project] 아파트 물품 공유 라이브러리 DB 구축 (Final Fixed)
-- ========================================================

-- 1. [초기화] 기존 세션 종료 및 DB/Role 전체 삭제 (Reset)
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'DB_Term_Project';

DROP DATABASE IF EXISTS "DB_Term_Project";

-- 기존 역할 삭제
DROP ROLE IF EXISTS db_superuser;
DROP ROLE IF EXISTS db_manager;
DROP ROLE IF EXISTS db_resident;

-- 세부 역할(추상 역할) 삭제
DROP ROLE IF EXISTS db_owner;
DROP ROLE IF EXISTS db_borrower;
DROP ROLE IF EXISTS db_delivery_partner;

-- 2. [역할 생성] 계정(Account)과 직무(Role) 정의

-- (A) 실제 로그인 계정
CREATE USER db_superuser WITH PASSWORD 'dev1234'; -- 개발자
ALTER USER db_superuser CREATEDB;

CREATE USER db_manager WITH PASSWORD 'manager1234'; -- 관리자 (운영진)
CREATE USER db_resident WITH PASSWORD 'resident1234'; -- 통합 사용자 (로그인용)

-- (B) 추상 역할 (로그인 불가, 권한 묶음용)
CREATE ROLE db_owner;            -- 📦 물품 소유자 역할
CREATE ROLE db_borrower;         -- 🙋 대여 희망자 역할
CREATE ROLE db_delivery_partner; -- 🚚 배송 파트너 역할

-- 3. [DB 생성]
CREATE DATABASE "DB_Term_Project" OWNER db_superuser;

-- ########################################################
-- [중요] 여기서부터는 'DB_Term_Project' 접속 후 실행
-- ########################################################

-- 4. [테이블 생성] Schema Creation

-- (1) 주민 테이블 (Residents)
CREATE TABLE Residents (
    resident_id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(200) NOT NULL,
    name VARCHAR(50) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    building VARCHAR(10) NOT NULL,
    unit VARCHAR(10) NOT NULL,
    points INTEGER DEFAULT 1000 CHECK (points >= 0),
    status VARCHAR(20) DEFAULT 'pending' 
        CHECK (status IN ('pending', 'approved', 'rejected')),
    is_manager BOOLEAN DEFAULT FALSE
);

-- (2) 물품 테이블 (Items)
-- 분쟁 시 'disputed' 상태로 잠금
CREATE TABLE Items (
    item_id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    description TEXT,
    rent_fee INTEGER DEFAULT 0 CHECK (rent_fee >= 0),
    expiration_date DATE DEFAULT '9999-12-31',
    status VARCHAR(20) DEFAULT 'available' 
        CHECK (status IN ('available', 'rented', 'pending', 'under_repair', 'disputed')),
    CONSTRAINT fk_owner FOREIGN KEY (owner_id) REFERENCES Residents(resident_id) ON DELETE CASCADE
);

-- (3) 대여 테이블 (Rentals)
-- 배송 라이프사이클 (waiting_driver -> ... -> completed)
CREATE TABLE Rentals (
    rental_id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL,
    borrower_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'requested'
        CHECK (status IN ('requested', 'approved', 'rejected', 'rented', 'returned', 'overdue', 'disputed')),
    delivery_option VARCHAR(10) CHECK (delivery_option IN ('pickup', 'delivery')),
    delivery_partner_id INTEGER,
    delivery_fee INTEGER DEFAULT 0 CHECK (delivery_fee >= 0),
    delivery_status VARCHAR(20) DEFAULT 'pending'
        CHECK (delivery_status IN (
            'pending', 'waiting_driver', 'accepted', 
            'picked_up', 'arrived', 'completed'
        )),
    CONSTRAINT fk_item FOREIGN KEY (item_id) REFERENCES Items(item_id) ON DELETE CASCADE,
    CONSTRAINT fk_borrower FOREIGN KEY (borrower_id) REFERENCES Residents(resident_id) ON DELETE CASCADE,
    CONSTRAINT fk_partner FOREIGN KEY (delivery_partner_id) REFERENCES Residents(resident_id) ON DELETE SET NULL
);

-- (4) 분쟁 테이블 (Disputes)
CREATE TABLE Disputes (
    dispute_id SERIAL PRIMARY KEY,
    rental_id INTEGER UNIQUE NOT NULL,
    manager_id INTEGER,
    reason TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    resolution TEXT,
    compensation_amount INTEGER DEFAULT 0 CHECK (compensation_amount >= 0),
    CONSTRAINT fk_rental FOREIGN KEY (rental_id) REFERENCES Rentals(rental_id) ON DELETE CASCADE,
    CONSTRAINT fk_manager FOREIGN KEY (manager_id) REFERENCES Residents(resident_id) ON DELETE SET NULL
);

-- 5. [뷰 생성] 매니저용
CREATE OR REPLACE VIEW View_Manager_Residents AS
SELECT resident_id, user_id, name, phone_number, building, unit, points, status, is_manager
FROM Residents;

-- 6. [권한 부여] Security & Permissions

-- [A] 기본 접속 허용
GRANT CONNECT ON DATABASE "DB_Term_Project" TO db_manager, db_resident;
GRANT USAGE ON SCHEMA public TO db_manager, db_resident, db_owner, db_borrower, db_delivery_partner;

-- [B] 세부 역할별 권한 정의 (RBAC Core)

-- 📦 1. 소유자 (Owner): 내 물건 관리, 대여 승인
GRANT SELECT, INSERT, UPDATE, DELETE ON Items TO db_owner; 
GRANT SELECT, UPDATE ON Rentals TO db_owner;
GRANT UPDATE (points) ON Residents TO db_owner; -- 수익 수취

-- 🙋 2. 대여자 (Borrower): 물건 검색, 신청, 분쟁 신고
GRANT SELECT ON Items TO db_borrower;
GRANT SELECT, INSERT, UPDATE ON Rentals TO db_borrower;
GRANT UPDATE (points) ON Residents TO db_borrower; -- 결제
GRANT INSERT ON Disputes TO db_borrower;

-- 🚚 3. 배송 파트너 (Delivery Partner): 배송 상태 변경
GRANT SELECT, UPDATE ON Rentals TO db_delivery_partner;
GRANT SELECT ON Items TO db_delivery_partner;
GRANT UPDATE (points) ON Residents TO db_delivery_partner; -- 배송비 수취

-- 🌍 4. 공통 권한 (중요!)
-- 본인 확인용 Residents 조회
GRANT SELECT ON Residents TO db_owner, db_borrower, db_delivery_partner;
-- 시퀀스 사용 (INSERT 시 필요)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO db_owner, db_borrower, db_delivery_partner;
-- [수정됨] 뷰 조회 권한 추가 (InsufficientPrivilege 오류 해결)
GRANT SELECT ON View_Manager_Residents TO db_owner, db_borrower, db_delivery_partner;

-- [C] 역할 상속 (Role Inheritance)
-- db_resident 계정은 위 3가지 역할을 모두 수행할 수 있습니다.
GRANT db_owner TO db_resident;
GRANT db_borrower TO db_resident;
GRANT db_delivery_partner TO db_resident;

-- [D] 매니저 (db_manager) 권한
-- 개인정보 보호(Residents 조회 불가) 정책 유지
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO db_manager;
GRANT DELETE ON Items, Rentals TO db_manager;
REVOKE DELETE ON Residents, Disputes FROM db_manager;
REVOKE SELECT ON Residents FROM db_manager; 
GRANT SELECT ON View_Manager_Residents TO db_manager; 
GRANT SELECT (resident_id) ON Residents TO db_manager; 
GRANT UPDATE (status) ON Residents TO db_manager;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO db_manager;