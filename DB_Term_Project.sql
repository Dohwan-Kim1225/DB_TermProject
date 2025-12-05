-- ========================================================
-- [Project] 아파트 물품 공유 라이브러리 DB 구축 (Final Complete Version)
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
CREATE USER db_superuser WITH PASSWORD 'dev1234'; -- 개발자 (모든 권한)
ALTER USER db_superuser CREATEDB;

CREATE USER db_manager WITH PASSWORD 'manager1234'; -- 관리자 (개인정보 보호 적용)
CREATE USER db_resident WITH PASSWORD 'resident1234'; -- 통합 사용자 (로그인용)

-- (B) 추상 역할 (로그인 불가, 권한 그룹핑용)
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
    is_manager BOOLEAN DEFAULT FALSE,
    is_delivery_banned BOOLEAN DEFAULT FALSE -- [New] 배송 알바 활동 정지 여부
);

-- (2) 물품 테이블 (Items)
-- [Update] 상태값 추가: disputed(분쟁), withdrawn(철회), expired(만료)
CREATE TABLE Items (
    item_id SERIAL PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    category VARCHAR(50),
    description TEXT,
    rent_fee INTEGER DEFAULT 0 CHECK (rent_fee >= 0),
    expiration_date DATE DEFAULT '9999-12-31',
    status VARCHAR(20) DEFAULT 'available' 
        CHECK (status IN ('available', 'rented', 'pending', 'under_repair', 'disputed', 'withdrawn', 'expired')),
    CONSTRAINT fk_owner FOREIGN KEY (owner_id) REFERENCES Residents(resident_id) ON DELETE CASCADE
);

-- (3) 대여 테이블 (Rentals)
-- [Update] 배송 및 반납 프로세스를 위한 상세 상태값 적용
CREATE TABLE Rentals (
    rental_id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL,
    borrower_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    
    -- 대여 진행 상태
    status VARCHAR(20) DEFAULT 'requested'
        CHECK (status IN ('requested', 'approved', 'rejected', 'rented', 'returned', 'overdue', 'disputed')),
    
    -- 배송 옵션 및 상태 (대여/반납 공용)
    delivery_option VARCHAR(10) CHECK (delivery_option IN ('pickup', 'delivery')),
    delivery_partner_id INTEGER,
    delivery_fee INTEGER DEFAULT 0 CHECK (delivery_fee >= 0),
    delivery_status VARCHAR(20) DEFAULT 'pending'
        CHECK (delivery_status IN (
            'pending',          -- 초기 상태
            'waiting_driver',   -- 기사 대기 (시장 등록됨)
            'accepted',         -- 기사 배정됨
            'picked_up',        -- 배송 출발
            'arrived',          -- 도착 (최종 확인 대기)
            'completed'         -- 배송 완료 (정산 끝)
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

-- 5. [뷰 생성] 매니저 및 일반 사용자용 정보 조회 뷰
-- 비밀번호 등 민감 정보를 제외하고, 배송 정지 여부(is_delivery_banned)를 포함한 뷰입니다.
CREATE OR REPLACE VIEW View_Manager_Residents AS
SELECT resident_id, user_id, name, phone_number, building, unit, points, status, is_manager, is_delivery_banned
FROM Residents;

-- 6. [권한 부여] Security & Permissions (RBAC)

-- [A] 기본 접속 허용
GRANT CONNECT ON DATABASE "DB_Term_Project" TO db_manager, db_resident;
GRANT USAGE ON SCHEMA public TO db_manager, db_resident, db_owner, db_borrower, db_delivery_partner;

-- [B] 세부 역할별 권한 정의 (기능 단위 분리)

-- 📦 1. 소유자 (Owner)
GRANT SELECT, INSERT, UPDATE, DELETE ON Items TO db_owner; 
GRANT SELECT, UPDATE ON Rentals TO db_owner;
GRANT UPDATE (points) ON Residents TO db_owner; -- 수익 수취
GRANT SELECT ON Disputes TO db_owner; -- 분쟁 내역 조회

-- 🙋 2. 대여자 (Borrower)
GRANT SELECT ON Items TO db_borrower;
GRANT SELECT, INSERT, UPDATE ON Rentals TO db_borrower;
GRANT UPDATE (points) ON Residents TO db_borrower; -- 결제
GRANT SELECT, INSERT ON Disputes TO db_borrower; -- 분쟁 신고 및 조회

-- 🚚 3. 배송 파트너 (Delivery Partner)
GRANT SELECT, UPDATE ON Rentals TO db_delivery_partner;
GRANT SELECT ON Items TO db_delivery_partner;
GRANT UPDATE (points) ON Residents TO db_delivery_partner; -- 배송비 수취

-- 🌍 4. 공통 권한 (필수)
-- 본인 확인용 Residents 조회
GRANT SELECT ON Residents TO db_owner, db_borrower, db_delivery_partner;
-- 시퀀스 사용 (INSERT 시 필요)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO db_owner, db_borrower, db_delivery_partner;
-- 뷰 조회 권한 (앱 로직 수행용)
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
REVOKE SELECT ON Residents FROM db_manager; -- ★ 핵심 보안 설정
GRANT SELECT ON View_Manager_Residents TO db_manager; 

-- 매니저 업무 수행을 위한 특정 컬럼 권한 (승인, 배송정지 등)
GRANT SELECT (resident_id, is_delivery_banned) ON Residents TO db_manager; 
GRANT UPDATE (status, is_delivery_banned) ON Residents TO db_manager;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO db_manager;