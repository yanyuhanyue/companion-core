# Official NGINX mainline; matches the native Windows proxy test version.
# https://github.com/docker-library/official-images/blob/master/library/nginx
FROM nginx:1.31.8-alpine@sha256:1ef7ca94d7879df82421428ce13bd1eaa412404953859cf7a5b6512c25e67a60
COPY deploy/console-ui.nginx.conf /etc/nginx/nginx.conf
COPY companion/console_static/index.html companion/console_static/app.js companion/console_static/app.css companion/console_static/icon.svg /usr/share/nginx/html/
USER 101:101
EXPOSE 8080
ENTRYPOINT ["nginx", "-g", "daemon off;"]
