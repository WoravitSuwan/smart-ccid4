# ใช้บน Linux/macOS หรือ Git Bash บน Windows
DC = docker compose
.PHONY: up down logs migrate seed test lint fmt css shell createsuperuser

up:            ## เปิดระบบ
	$(DC) up -d --build
down:
	$(DC) down
logs:
	$(DC) logs -f web worker
migrate:
	$(DC) exec web python manage.py migrate
seed:          ## ผู้ใช้ตัวอย่างทุกบทบาท (เฉพาะฐานข้อมูลทดลอง)
	$(DC) exec web python manage.py seed_demo --force && $(DC) exec web python manage.py seed_cases --force
createsuperuser:
	$(DC) exec web python manage.py createsuperuser
test:          ## ต้องมี PostgreSQL (DATABASE_URL) ในเครื่องที่รันเทสต์
	python -m pytest
lint:
	ruff check . && black --check .
fmt:
	ruff check --fix . && black .
css:           ## build Tailwind (ต้องมี node ตอนพัฒนาเท่านั้น)
	npx tailwindcss -i static/src/input.css -o static/css/app.css --minify
shell:
	$(DC) exec web python manage.py shell
