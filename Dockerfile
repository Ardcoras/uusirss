FROM jazzdd/alpine-flask:python3

COPY . /app
COPY custom_entrypoint.sh /custom_entrypoint.sh
RUN chmod 0777 /custom_entrypoint.sh

ENTRYPOINT ["/custom_entrypoint.sh"]
