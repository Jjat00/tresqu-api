release: python manage.py migrate && python manage.py createcachetable
web: gunicorn cashbotapp.wsgi --timeout 180 --workers 2 --threads 4