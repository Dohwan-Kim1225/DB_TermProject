-- ========================================================
-- [Project] 아파트 물품 공유 라이브러리 DB 구축 스크립트
-- ========================================================

-- 1. [초기화] 기존 세션 종료 및 DB/Role 삭제 (Reset)
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'DB_Term_Project';

DROP DATABASE IF EXISTS "DB_Term_Project";
DROP ROLE IF EXISTS db_superuser;
DROP ROLE IF EXISTS db_manager;
DROP ROLE IF EXISTS db_resident;

-- 2. [역할 생성] 3-Tier Security Roles
-- (1) 개발자 (Superuser)
CREATE USER db_superuser WITH PASSWORD 'dev1234';
ALTER USER db_superuser CREATEDB;
-- (2) 매니저 (운영진)
CREATE USER db_manager WITH PASSWORD 'manager1234';
-- (3) 일반 주민 (사용자)
CREATE USER db_resident WITH PASSWORD 'resident1234';

-- 3. [DB 생성] 데이터베이스 만들기
CREATE DATABASE "DB_Term_Project" OWNER db_superuser;

-- ########################################################
-- 주의: 아래 스크립트는 'DB_Term_Project'에 접속한 상태에서 실행되어야 합니다.
-- DBeaver에서 DB 연결을 확인하세요.
-- ########################################################

-- 4. [테이블 생성] Schema Creation

-- (1) 주민 테이블 (주소 분리, 포인트, 상태 관리)
CREATE TABLE Residents (
    resident_id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) UNIQUE NOT NULL,
    password VARCHAR(200) NOT NULL,
    name VARCHAR(50) NOT NULL,
    phone_number VARCHAR(20) UNIQUE NOT NULL,
    building VARCHAR(10) NOT NULL,
    unit VARCHAR(10) NOT NULL,
    points INTEGER DEFAULT 1000 CHECK (points >= 0),
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    is_manager BOOLEAN DEFAULT FALSE
);

-- (2) 물품 테이블 (만료일 추가, 보증금 삭제)
CREATE TABLE Items (
    item_id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    description TEXT,
    rent_fee INTEGER DEFAULT 0 CHECK (rent_fee >= 0),
    expiration_date DATE DEFAULT '9999-12-31',
    status VARCHAR(20) DEFAULT 'available' 
        CHECK (status IN ('available', 'rented', 'pending', 'under_repair')),
    CONSTRAINT fk_owner FOREIGN KEY (owner_id) REFERENCES Residents(resident_id) ON DELETE CASCADE
);

-- (3) 대여 테이블 (배송비 추가)
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
    delivery_status VARCHAR(20) DEFAULT 'pending',
    CONSTRAINT fk_item FOREIGN KEY (item_id) REFERENCES Items(item_id) ON DELETE CASCADE,
    CONSTRAINT fk_borrower FOREIGN KEY (borrower_id) REFERENCES Residents(resident_id) ON DELETE CASCADE,
    CONSTRAINT fk_partner FOREIGN KEY (delivery_partner_id) REFERENCES Residents(resident_id) ON DELETE SET NULL
);

-- (4) 분쟁 테이블 (손해배상 추가)
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

-- 5. [뷰 생성] 매니저용 프라이버시 뷰 (비밀번호 숨김)
CREATE OR REPLACE VIEW View_Manager_Residents AS
SELECT resident_id, user_id, name, phone_number, building, unit, points, status, is_manager
FROM Residents;

-- 6. [권한 부여] Authorization & Security

-- [A] 기본 접속 허용
GRANT CONNECT ON DATABASE "DB_Term_Project" TO db_manager, db_resident;
GRANT USAGE ON SCHEMA public TO db_manager, db_resident;

-- [B] 매니저 (db_manager) 권한
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO db_manager;
GRANT DELETE ON Items, Rentals TO db_manager;
REVOKE DELETE ON Residents, Disputes FROM db_manager; -- 중요 기록 삭제 방지
REVOKE SELECT ON Residents FROM db_manager; -- 개인정보 보호 (원본 조회 불가)
GRANT SELECT ON View_Manager_Residents TO db_manager; -- 뷰 조회 허용
-- (필수) 승인 업무를 위한 특정 컬럼 접근 허용
GRANT SELECT (resident_id) ON Residents TO db_manager; 
GRANT UPDATE (status) ON Residents TO db_manager;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO db_manager;

-- [C] 일반 주민 (db_resident) 권한
GRANT SELECT ON ALL TABLES IN SCHEMA public TO db_resident;
GRANT INSERT ON Residents, Items, Rentals, Disputes TO db_resident;
GRANT UPDATE ON Residents, Items, Rentals TO db_resident;
REVOKE UPDATE, DELETE ON Disputes FROM db_resident; -- 분쟁 수정 불가
REVOKE DELETE ON Residents FROM db_resident; -- 탈퇴 불가 (상태변경만)
-- (필수) 로그인 시 본인 확인을 위해 Residents 조회 허용
GRANT SELECT ON Residents TO db_resident;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO db_resident;