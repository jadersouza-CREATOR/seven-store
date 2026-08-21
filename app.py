from flask import Flask, render_template, request, redirect, session
import sqlite3

app = Flask(__name__)
app.secret_key = "seven_store_versao_05"


def conectar_banco():
    banco = sqlite3.connect("banco.db")
    banco.row_factory = sqlite3.Row
    return banco


def criar_banco():
    banco = conectar_banco()

    banco.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL
        )
    """)

    banco.commit()
    banco.close()


@app.route("/")
def login():
    if "usuario" in session:
        return redirect("/inicio")

    return render_template("login.html")


@app.route("/login", methods=["POST"])
def fazer_login():
    usuario = request.form["usuario"]
    senha = request.form["senha"]

    banco = conectar_banco()

    resultado = banco.execute(
        "SELECT * FROM usuarios WHERE usuario = ? AND senha = ?",
        (usuario, senha)
    ).fetchone()

    banco.close()

    if resultado:
        session["usuario"] = usuario
        return redirect("/inicio")

    return render_template(
        "login.html",
        erro="Usuário ou senha incorretos."
    )


@app.route("/cadastro")
def cadastro():
    return render_template("cadastro.html")


@app.route("/cadastrar", methods=["POST"])
def cadastrar():
    usuario = request.form["usuario"]
    senha = request.form["senha"]

    banco = conectar_banco()

    try:
        banco.execute(
            "INSERT INTO usuarios (usuario, senha) VALUES (?, ?)",
            (usuario, senha)
        )

        banco.commit()
        banco.close()

        return redirect("/")

    except sqlite3.IntegrityError:
        banco.close()

        return render_template(
            "cadastro.html",
            erro="Esse usuário já existe."
        )


@app.route("/inicio")
def inicio():
    if "usuario" not in session:
        return redirect("/")

    return render_template(
        "inicio.html",
        usuario=session["usuario"]
    )


@app.route("/sair")
def sair():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    criar_banco()
    app.run(debug=True)