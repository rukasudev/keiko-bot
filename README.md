# DESAFIO-STONE-RESTFUL-API

API desenvolvida no desafio técnico da Stone Pagamentos para a vaga de Desenvolvedor Backend.
Desenvolvido por Lucas Daniel: https://www.linkedin.com/in/lucasdanielld/

Principais libs utilizadas:
1. Flask-Migrate - trabalhar com as "migrations" do banco.
2. Flask-RESTfulx - biblioteca para RESTFUL API e Swagger Documentation.
3. Flask-Script - trabalhar com scripts externos que acessam o Flask.
4. Flask-SQLAlchemy - trabalhar com SQLAlchemy ORM.

Estrutura do projeto:
```
.
├── README.md
├── app
│   ├── __init__.py
│   ├── main
│   │   ├── __init__.py
│   │   ├── controller
│   │   │   ├── (all controllers.py)
│   │   ├── model
│   │   │   ├── (all models.py)
│   │   ├── service
│   │   │   ├── (all services.py)
│   │   └── util
│   │   │   ├── __init__.py
│   │   │   ├── decorator.py
│   │   │   ├── dto.py
│   │   │── config.py
│   └── test
│       ├── __init__.py
│       ├── (all tests*.py)
├── manage.py
├── requirements.txt
├── .dockerignore
├── docker-compose.yml
└── Dockerfile
```

* controller - diretório onde fica todos os controllers dos endpoints.
* model - diretório onde fica todos os models do banco.
* service - diretório onde todos os serviços da aplicação.
* manage.py - scripts para gerenciar a aplicação (migrations, execução da aplicação, etc.)

## Execução

1. Clone do repositório.
2. pip install -r requirements.txt
3. Rodar os seguintes comandos para criar a estrutura do banco de dados:
    1. python manage.py db init
    2. python manage.py db migrate
    3. python manage.py db upgrade
4. Inicialize o servidor rodando "python manage.py runserver"

## Execução via Docker Compose

1. CD no diretório raiz do repositório.
2. docker-compose up


## Testes
Para testar a aplicação, executar o seguinte comando dentro da pasta raiz do projeto:
> python manage.py test

## Swagger Documentation
Para consulta via documentação Swagger é necessário acessar a seguinte URL:
> http://127.0.0.1:5000

## Endpoints
### Usuários
**[GET] Retorna todos os usuários cadastrados**
> hostname:port/api/v1/users/

**Exemplo de resposta**
```json
{
  "data": [
    {
      "email": "lucas.daiel@teste.com",
      "name": "Lucas Daniel",
      "password": "123456",
      "public_id": "89ee8116-026e-4f50-a23e-7575bbb36d04"
    }
  ]
}
```
**[GET] Retorna o usuário pelo seu ID público**
> hostname:port/api/v1/users/<public_id>

**Exemplo de resposta**
```json
{
  "data": [
    {
      "email": "lucas.daiel@teste.com",
      "name": "Lucas Daniel",
      "password": "123456",
      "public_id": "89ee8116-026e-4f50-a23e-7575bbb36d04"
    }
  ]
}
```
**[POST] Criar um novo usuário**
> hostname:port/api/v1/users/

**Exemplo do corpo**
```json
{
  "email": "lucas.daniel@teste.com",
  "name": "Lucas Daniel",
  "password": "123456"
}
```
**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Successfully logged in.",
  "Authorization": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE2MTg2OTAyNTcsImlhdCI6MTYxODYwMzg1Miwic3ViIjoyfQ.zKNNMDqUkeD9gAG4gzYRoY0AGvRqZRqjiux5KWKDFLY"
}
```
### Auth
**[POST] Autenticação de Usuário**
> hostname:port/api/v1/auth/login/

**Exemplo do corpo**
```json
{
  "email": "lucas.daniel@teste.com",
  "password": "123456"
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Successfully logged in.",
  "Authorization": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJleHAiOjE2MTg1MDY3OTcsImlhdCI6MTYxODQyMDM5Miwic3ViIjoxfQ.0IyoDv8EZDMBrF_jJdRIySjIA3_nI1Rd3npRkUq92O8"
}
```

**[POST] Logout de Usuário**
> hostname:port/api/v1/auth/logout/

**Exemplo do corpo**
```json
{
  "email": "lucas.daniel@teste.com",
  "password": "123456"
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Successfully logged out."
}
```
### Vendedores
**[GET] Retorna todos os vendedores cadastrados**
> hostname:port/api/v1/vendedores/

**Exemplo de resposta**
```json
{
  "data": [
    {
      "name": "Joao",
      "email": "joao@desafio.com",
      "public_id": "bf14a5d6-b095-43bb-8c53-f3d992a1f8a0",
      "associated_route": "Centro do Rio de Janeiro",
      "customers": [
        {
          "name": "Mercado Mundial",
          "longitude": -43.213297,
          "latitude": -22.913357,
          "public_id": "9f67682b-773a-4ebf-8285-98fd734602e6"
        }
      ]
    },
    {
      "name": "Matheus",
      "email": "matheus@sdesafio.com",
      "public_id": "f964358a-e29c-4272-97e0-db167ce0ae37",
      "associated_route": "Copacabana",
      "customers": [
        {
          "name": "Espetto Carioca",
          "longitude": -43.180508,
          "latitude": -22.969152,
          "public_id": "064d2c27-c5d5-4ac6-b477-4a763c2f212e"
        },
        {
          "name": "Mercado Extra",
          "longitude": -43.188305,
          "latitude": -22.967227,
          "public_id": "feeccff5-5c72-4173-a01c-3c312ba83983"
        }
      ]
    },
    {
      "name": "Lucas",
      "email": "lucas@desafio.com",
      "public_id": "63e56c68-b2ab-4e7f-b372-47c0f1a93e0a",
      "associated_route": "Macaé",
      "customers": []
    },
    {
      "name": "Outros",
      "email": "Outros",
      "public_id": "4a2af44a-56ee-45c3-8e1d-cef8b5eeb3cd",
      "associated_route": "Outros",
      "customers": [
        {
          "name": "Mercado Guanabara",
          "longitude": -43.782131,
          "latitude": -22.869824,
          "public_id": "cdbd9955-b1e4-478f-bdcc-5f7471251abd"
        }
      ]
    }
  ]
}
```
**[GET] Retorna um vendedor pelo seu ID público**
> hostname:port/api/v1/vendedores/<public_id>

**Exemplo de resposta**
```json
{
  "name": "Joao",
  "email": "joao@stone.com",
  "public_id": "bf14a5d6-b095-43bb-8c53-f3d992a1f8a0",
  "associated_route": "Centro do Rio de Janeiro",
  "customers": [
    {
      "name": "Mercado Mundial",
      "longitude": -43.213297,
      "latitude": -22.913357,
      "public_id": "9f67682b-773a-4ebf-8285-98fd734602e6"
    }
  ]
}
```
**[POST] Cadastra um novo vendedor**
> hostname:port/api/v1/vendedores/

**Exemplo do corpo**
```json
{
  "name": "Stone",
  "email": "stone@stone.com"
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Vendedor successfully registered."
}
```
**[PUT] Edita um vendedor pelo seu ID público**
> hostname:port/api/v1/vendedores/<public_id>

**Exemplo do corpo**
```json
{
  "name": "Stone",
  "email": "stone@stone.com"
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Vendedor successfully edited."
}
```
**[DELETE] Deleta um vendedor pelo seu ID público**
> hostname:port/api/v1/vendedores/<public_id>

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Vendedor successfully deleted."
}
```
### Clientes
**[GET] Retorna todos os clientes cadastrados**
> hostname:port/api/v1/clientes/

**Exemplo de resposta**
```json
{
  "data": [
    {
      "name": "Mercado Mundial",
      "latitude": -22.913357,
      "longitude": -43.213297,
      "public_id": "9f67682b-773a-4ebf-8285-98fd734602e6"
    },
    {
      "name": "Espetto Carioca",
      "latitude": -22.969152,
      "longitude": -43.180508,
      "public_id": "064d2c27-c5d5-4ac6-b477-4a763c2f212e"
    },
    {
      "name": "Mercado Extra",
      "latitude": -22.967227,
      "longitude": -43.188305,
      "public_id": "feeccff5-5c72-4173-a01c-3c312ba83983"
    },
    {
      "name": "Mercado Guanabara",
      "latitude": -22.869824,
      "longitude": -43.782131,
      "public_id": "cdbd9955-b1e4-478f-bdcc-5f7471251abd"
    }
  ]
}
```
**[GET] Retorna clientes utilizando Querystring**
> hostname:port/api/v1/clientes?vendedor=matheus|joao&&rota=outros

**Exemplo de resposta**
```json
{
  "data": [
    {
      "name": "Espetto Carioca",
      "latitude": -22.969152,
      "longitude": -43.180508,
      "public_id": "064d2c27-c5d5-4ac6-b477-4a763c2f212e"
    },
    {
      "name": "Mercado Extra",
      "latitude": -22.967227,
      "longitude": -43.188305,
      "public_id": "feeccff5-5c72-4173-a01c-3c312ba83983"
    },
    {
      "name": "Mercado Guanabara",
      "latitude": -22.869824,
      "longitude": -43.782131,
      "public_id": "cdbd9955-b1e4-478f-bdcc-5f7471251abd"
    }
  ]
}
```
**[GET] Retorna um cliente pelo seu ID público**
> hostname:port/api/v1/clientes/<public_id>

**Exemplo de resposta**
```json
[
  {
    "name": "Mercado Mundial",
    "latitude": -22.913357,
    "longitude": -43.213297,
    "public_id": "9f67682b-773a-4ebf-8285-98fd734602e6"
  }
]
```
**[POST] Cadastra um novo cliente**
> hostname:port/api/v1/clientes/

**Exemplo do corpo**
```json
{
  "name": "Mercado Campeão",
  "longitude": -43.558697,
  "latitude": -22.88691
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Cliente successfully registered."
}
```
**[PUT] Edita um cliente pelo seu ID público**
> hostname:port/api/v1/clientes/<public_id>

**Exemplo do corpo**
```json
{
  "name": "Mercado Mundial",
  "longitude": -43.558697,
  "latitude": -22.88691
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Cliente successfully edited."
}
```
**[DELETE] Deleta um cliente pelo seu ID público**
> hostname:port/api/v1/clientes/<public_id>

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Cliente successfully deleted."
}
```
### Rotas
**[POST] Cadastra uma nova rota**
> hostname:port/api/v1/rotas/

**Exemplo do corpo**
```json
{
  "name": "Belford Roxo",
  "coordinates":{
    "type": "FeatureCollection",
    "features": [
      {      
	"type": "Feature",
	"properties": {},
	"geometry": {
	  "type": "Polygon",
	  "coordinates": [
	    [
	      [
		-43.464317321777344,
		-22.780929700611292
	      ],
	      [
		-43.35651397705078,
		-22.780929700611292
	      ],
	      [
		-43.35651397705078,
		-22.756553877730898
	      ],
	      [
		-43.464317321777344,
		-22.756553877730898
	      ],
	      [
		-43.464317321777344,
		-22.780929700611292
	      ]
	    ]
          ]
	}    
      }
    ]
  }
}
```
**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Rota successfully registered."
}
```
**[PUT] Edita uma Rota pelo seu ID público**
> hostname:port/api/v1/rotas/<public_id>

**Exemplo do corpo**
```json
{
  "name": "Belford Roxo Editado",
  "coordinates":{
    "type": "FeatureCollection",
    "features": [
      {      
	"type": "Feature",
	"properties": {},
	"geometry": {
	  "type": "Polygon",
	  "coordinates": [
	    [
	      [
		-43.464317321777344,
		-22.780929700611292
	      ],
	      [
		-43.35651397705078,
		-22.780929700611292
	      ],
	      [
		-43.35651397705078,
		-22.756553877730898
	      ],
	      [
		-43.464317321777344,
		-22.756553877730898
	      ],
	      [
		-43.464317321777344,
		-22.780929700611292
	      ]
	    ]
          ]
	}    
      }
    ]
  }
}
```

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Rota successfully edited."
}
```
**[DELETE] Deleta uma rota pelo seu ID público**
> hostname:port/api/v1/rotas/<public_id>

**Exemplo de resposta**
```json
{
  "status": "success",
  "message": "Rota successfully deleted."
}
```
**[POST] Associa um vendedor a rota pelo seu ID público**
> hostname:port/api/v1/rotas/ad4ffb21-c1a6-4570-a37f-2b0ea361cc0b/vendedor/

**Exemplo do corpo**
```json
{
  "vendedor": "df4a4cc3-7014-4a12-b71d-36e03ba90833"
}
```

**Exemplo de resposta**
```json
{
  "status": "sucess",
  "message": "Vendedor successfully associated."
}
```
**[DELETE] Disassocia um vendedor da rota pelo seu ID público**
> hostname:port/api/v1/rotas/ad4ffb21-c1a6-4570-a37f-2b0ea361cc0b/vendedor/

**Exemplo de resposta**
```json
{
  "status": "sucess",
  "message": "Vendedor successfully disassociated."
}
```
