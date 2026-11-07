FROM nginx:1.27-alpine

RUN apk add --no-cache openssl \
 && mkdir -p /etc/nginx/certs \
 && openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
      -keyout /etc/nginx/certs/ticketforge.key \
      -out /etc/nginx/certs/ticketforge.crt \
      -subj "/C=PK/ST=Sindh/L=Karachi/O=TicketForge/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
 && chmod 600 /etc/nginx/certs/ticketforge.key

COPY nginx.conf /etc/nginx/nginx.conf
COPY proxy_params_ticketforge.conf /etc/nginx/proxy_params_ticketforge.conf
