FROM python:3.10

WORKDIR /usr/src/workspace

COPY requirements.txt .

RUN python -m pip install git+https://github.com/Rapptz/discord.py.git@3bca40352ecfa5b219bae0527252ee5d296113e7

RUN pip install -r requirements.txt

ARG APPLICATION_ENV
ENV APPLICATION_ENV=${APPLICATION_ENV}

COPY . .

CMD ["python", "./__main__.py"]
