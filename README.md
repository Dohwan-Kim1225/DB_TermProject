# 🏢 아파트 물품 공유 라이브러리 (Apartment Goods Sharing Platform)

아파트 입주민들끼리 유휴 물품(공구, 캠핑용품 등)을 공유하고, 배송 대행을 통해 수익을 창출하는 데이터베이스 중심의 웹 플랫폼입니다.

## 📖 프로젝트 소개
단순한 게시판 형태를 넘어, **'포인트 트랜잭션'**, **'배송 상태 추적'**, **'분쟁 해결 프로세스'**를 갖춘 완성도 높은 C2C 거래 시스템입니다. 
데이터베이스의 **무결성(Integrity)**과 **보안(Security)**을 최우선으로 설계하였으며, PostgreSQL의 강력한 기능을 활용하여 비즈니스 로직을 구현했습니다.

## 🛠️ 기술 스택 (Tech Stack)
- **Frontend:** HTML5, CSS (Bootstrap 5), Jinja2 Template
- **Backend:** Python Flask
- **Database:** PostgreSQL (psycopg2)
- **Architecture:** MVC Pattern, 3-Tier Architecture

## ✨ 주요 기능 (Key Features)

### 1. 사용자 역할 (Dynamic Roles)
모든 승인된 주민은 아래 세 가지 역할을 동시에 수행할 수 있습니다.
- **📦 소유자:** 물품 등록, 대여 승인/거절, 반납 검수, 분쟁 신고
- **🙋‍♂️ 대여자:** 카테고리별 검색, 대여/반납 신청 (직거래/배송 선택), 이력 조회
- **🚚 배송 파트너:** 배송 콜 수락, 배송 상태 업데이트(픽업/도착), 수익 정산

### 2. 관리자 기능 (Admin)
- **회원 관리:** 신규 가입 승인 및 악성 유저 활동 정지 (배송 권한 박탈 등)
- **분쟁 판결:** 파손/분실 신고 건에 대한 귀책 사유 판단 및 배상금 강제 이체 처리
- **보안:** 개인정보 보호를 위해 비밀번호 등 민감 정보에는 접근할 수 없도록 설계됨

### 3. 데이터베이스 특화 기능
- **RBAC (Role-Based Access Control):** DB 계정을 `db_owner`, `db_borrower` 등으로 세분화하여 권한 관리
- **Transaction:** 포인트 이동, 상태 변경, 이력 생성이 원자적(Atomic)으로 처리됨
- **View:** 매니저의 개인정보 접근 제어를 위한 `View_Manager_Residents` 활용
- **State Machine:** 물품 및 대여 상태의 정교한 흐름 제어 (Available ↔ Rented ↔ Disputed)

## 💾 설치 및 실행 방법 (Installation)

### 1. 데이터베이스 구축
PostgreSQL이 설치된 환경에서 아래 SQL 스크립트를 실행하여 DB와 계정을 생성합니다.
```bash
# psql 또는 DBeaver 등에서 실행
DB_Term_Project_Final.sql
