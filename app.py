from flask import Flask, render_template, request, redirect, session
import sqlite3
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "seven_store_versao_06")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BANCO = os.path.join(BASE_DIR, "banco.db")


def conectar_banco():
    banco = sqlite3.connect(BANCO, timeout=10)
    banco.row_factory = sqlite3.Row
    criar_tabelas(banco)
    return banco


def criar_tabelas(banco):
    banco.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL
        )
    """)
    banco.commit()


def criar_banco():
    banco = sqlite3.connect(BANCO, timeout=10)
    criar_tabelas(banco)
    banco.close()


criar_banco()


@app.route("/")
def login():
    if "usuario" in session:
        return redirect("/inicio")
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def fazer_login():
    usuario = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "")

    if not usuario or not senha:
        return render_template("login.html", erro="Preencha usuário e senha.")

    banco = conectar_banco()
    try:
        resultado = banco.execute(
            "SELECT * FROM usuarios WHERE usuario = ? AND senha = ?",
            (usuario, senha)
        ).fetchone()
    finally:
        banco.close()

    if resultado:
        session["usuario"] = usuario
        return redirect("/inicio")

    return render_template("login.html", erro="Usuário ou senha incorretos.")


@app.route("/cadastro")
def cadastro():
    return render_template("cadastro.html")


@app.route("/cadastrar", methods=["POST"])
def cadastrar():
    usuario = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "")

    if not usuario or not senha:
        return render_template("cadastro.html", erro="Preencha usuário e senha.")

    banco = conectar_banco()
    try:
        banco.execute(
            "INSERT INTO usuarios (usuario, senha) VALUES (?, ?)",
            (usuario, senha)
        )
        banco.commit()
    except sqlite3.IntegrityError:
        return render_template("cadastro.html", erro="Esse usuário já existe.")
    finally:
        banco.close()

    return redirect("/")


@app.route("/inicio")
def inicio():
    if "usuario" not in session:
        return redirect("/")
    return render_template("inicio.html", usuario=session["usuario"])


@app.route("/produtos")
def produtos():
    return pagina_menu("Produtos", "Aqui você poderá cadastrar e gerenciar os produtos.")


@app.route("/clientes")
def clientes():
    return pagina_menu("Clientes", "Aqui você poderá cadastrar e gerenciar os clientes.")


@app.route("/vendas")
def vendas():
    return pagina_menu("Vendas", "Aqui você poderá registrar e consultar vendas.")


@app.route("/estoque")
def estoque():
    return pagina_menu("Estoque", "Aqui você poderá controlar o estoque da loja.")


@app.route("/relatorios")
def relatorios():
    return pagina_menu("Relatórios", "Aqui você poderá consultar os relatórios do sistema.")


@app.route("/caixa")
def caixa():
    return pagina_menu("Caixa", "Aqui você poderá acompanhar o caixa da loja.")


def pagina_menu(titulo, descricao):
    if "usuario" not in session:
        return redirect("/")
    return render_template(
        "pagina.html",
        titulo=titulo,
        descricao=descricao,
        usuario=session["usuario"]
    )


@app.route("/sair")
def sair():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False
    )
