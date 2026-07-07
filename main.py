import os

from fastapi import FastAPI
from sqlmodel import Field, Session, SQLModel, create_engine, select

import logfire


class Hero(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    secret_name: str
    age: int | None = Field(default=None, index=True)


# The database URL is automatically injected as an environment variable
engine = create_engine(os.getenv("DATABASE_URL"))


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


app = FastAPI()

# FastAPI Cloud injects this when the Logfire integration is connected.
logfire.configure(token=os.environ["LOGFIRE_TOKEN"])
logfire.instrument_fastapi(app)
logfire.instrument_sqlalchemy(engine=engine)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()




@app.post("/heroes/")
def create_hero(hero: Hero):
    with Session(engine) as session:
        session.add(hero)
        session.commit()
        session.refresh(hero)
        return hero


@app.get("/heroes/")
def read_heroes():
    with Session(engine) as session:
        heroes = session.exec(select(Hero)).all()
        return heroes


@app.get("/hello")
async def hello(name: str = "world"):
    logfire.info("Saying hello", name=name)
    return {"message": f"hello {name}"}