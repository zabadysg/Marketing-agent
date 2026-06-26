.PHONY: dev dev-build test test-local lint migrate migrate-create migrate-down \
        logs down clean ssl-init ssl-renew

DOMAIN ?= marketing.yourdomain.com
CERTBOT_EMAIL ?= admin@yourdomain.com

dev:
	docker compose up

dev-build:
	docker compose up --build

test:
	docker compose run --rm app pytest tests/ -v

test-local:
	pytest tests/ -v

lint:
	ruff check app/ tests/

migrate:
	docker compose run --rm app alembic upgrade head

migrate-create:
	@read -p "Migration name: " name; \
	docker compose run --rm app alembic revision --autogenerate -m "$$name"

migrate-down:
	docker compose run --rm app alembic downgrade -1

logs:
	docker compose logs -f app

down:
	docker compose down

clean:
	docker compose down -v --remove-orphans

nginx-render:
	DOMAIN=$(DOMAIN) envsubst '$${DOMAIN}' < nginx/nginx.conf.template > nginx/nginx.conf
	@echo "Rendered nginx/nginx.conf for domain: $(DOMAIN)"

ssl-init: nginx-render
	docker compose -f docker-compose.yml -f docker-compose.nginx.yml run --rm certbot \
		certonly --webroot --webroot-path=/var/www/certbot \
		--email $(CERTBOT_EMAIL) --agree-tos --no-eff-email \
		-d $(DOMAIN)
	docker compose -f docker-compose.yml -f docker-compose.nginx.yml up -d nginx

ssl-renew:
	docker compose -f docker-compose.yml -f docker-compose.nginx.yml run --rm certbot renew
	docker compose -f docker-compose.yml -f docker-compose.nginx.yml exec nginx nginx -s reload
