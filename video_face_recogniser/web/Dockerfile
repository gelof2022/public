FROM nginx:1.29-alpine
COPY --chmod=644 nginx.conf /etc/nginx/conf.d/default.conf
COPY --chmod=644 index.html app.js review.js scene-review.js redetection.js settings.js styles.css /usr/share/nginx/html/
