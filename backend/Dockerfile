FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# gunicorn et non runserver (serveur de développement). Réglages et raisons :
# gunicorn.conf.py.
CMD ["sh", "-c", "python manage.py migrate --no-input && exec gunicorn -c gunicorn.conf.py config.wsgi:application"]
