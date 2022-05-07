FROM python:3.9
FROM app/main.py

RUN mkdir -p /usr/src/bot
WORKDIR /usr/src/bot

COPY . .

CMD [ "python3", "app/main.py" ]