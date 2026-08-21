from flask import Flask, render_template, request, redirect, session
import os
import json
import urllib.request
import urllib.parse
import psycopg2
from psycopg2 import errors
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "seven_store_versao_07")


def conectar_banco():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurada no Render.")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)


def criar_banco():
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    id SERIAL PRIMARY KEY,
                    usuario VARCHAR(100) UNIQUE NOT NULL,
                    senha VARCHAR(255) NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS integracoes (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                    canal VARCHAR(30) NOT NULL,
                    account_id VARCHAR(150),
                    page_id VARCHAR(150),
                    phone_number_id VARCHAR(150),
                    business_account_id VARCHAR(150),
                    UNIQUE(usuario_id, canal)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversas (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                    canal VARCHAR(30) NOT NULL,
                    identificador VARCHAR(200) NOT NULL,
                    nome VARCHAR(200),
                    status VARCHAR(30) DEFAULT 'aberta',
                    ultima_mensagem TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mensagens (
                    id SERIAL PRIMARY KEY,
                    conversa_id INTEGER REFERENCES conversas(id) ON DELETE CASCADE,
                    direcao VARCHAR(20) NOT NULL,
                    texto TEXT NOT NULL,
                    externa_id VARCHAR(200),
                    criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        banco.commit()
    finally:
        banco.close()


criar_banco()


def usuario_id_atual():
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT id FROM usuarios WHERE usuario=%s", (session["usuario"],))
            row = cursor.fetchone()
            return row["id"] if row else None
    finally:
        banco.close()


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
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE usuario=%s AND senha=%s", (usuario, senha))
            resultado = cursor.fetchone()
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
        with banco.cursor() as cursor:
            cursor.execute("INSERT INTO usuarios (usuario, senha) VALUES (%s, %s)", (usuario, senha))
        banco.commit()
    except errors.UniqueViolation:
        banco.rollback()
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
    return render_template("pagina.html", titulo=titulo, descricao=descricao, usuario=session["usuario"])


@app.route("/atendimento")
def atendimento():
    if "usuario" not in session:
        return redirect("/")
    uid = usuario_id_atual()
    conversa_id = request.args.get("conversa", type=int)
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM conversas WHERE usuario_id=%s ORDER BY id DESC", (uid,))
            conversas = cursor.fetchall()
            atual = None
            mensagens = []
            if conversa_id:
                cursor.execute("SELECT * FROM conversas WHERE id=%s AND usuario_id=%s", (conversa_id, uid))
                atual = cursor.fetchone()
                if atual:
                    cursor.execute("SELECT * FROM mensagens WHERE conversa_id=%s ORDER BY criada_em", (conversa_id,))
                    mensagens = cursor.fetchall()
            cursor.execute("SELECT * FROM integracoes WHERE usuario_id=%s", (uid,))
            integracoes = {r["canal"]: r for r in cursor.fetchall()}
    finally:
        banco.close()
    return render_template(
        "atendimento.html", usuario=session["usuario"], conversas=conversas,
        conversa_atual=atual, mensagens=mensagens, conversa_selecionada=conversa_id,
        whatsapp_status="Conectado" if os.environ.get("WHATSAPP_ACCESS_TOKEN") and "whatsapp" in integracoes else "Aguardando configuração",
        instagram_status="Conectado" if os.environ.get("INSTAGRAM_ACCESS_TOKEN") and "instagram" in integracoes else "Aguardando configuração"
    )


@app.route("/configuracoes")
def configuracoes():
    if "usuario" not in session:
        return redirect("/")
    uid = usuario_id_atual()
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM integracoes WHERE usuario_id=%s", (uid,))
            dados = {r["canal"]: r for r in cursor.fetchall()}
    finally:
        banco.close()
    return render_template("configuracoes.html", usuario=session["usuario"],
        whatsapp=dados.get("whatsapp", {}), instagram=dados.get("instagram", {}),
        whatsapp_configurado="whatsapp" in dados, instagram_configurado="instagram" in dados)


@app.route("/configuracoes/salvar", methods=["POST"])
def salvar_configuracao():
    if "usuario" not in session:
        return redirect("/")
    uid = usuario_id_atual()
    canal = request.form.get("canal")
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            if canal == "whatsapp":
                cursor.execute("""INSERT INTO integracoes (usuario_id,canal,phone_number_id,business_account_id)
                    VALUES (%s,'whatsapp',%s,%s) ON CONFLICT (usuario_id,canal) DO UPDATE SET phone_number_id=EXCLUDED.phone_number_id,business_account_id=EXCLUDED.business_account_id""",
                    (uid, request.form.get("phone_number_id", "").strip(), request.form.get("business_account_id", "").strip()))
            elif canal == "instagram":
                cursor.execute("""INSERT INTO integracoes (usuario_id,canal,account_id,page_id)
                    VALUES (%s,'instagram',%s,%s) ON CONFLICT (usuario_id,canal) DO UPDATE SET account_id=EXCLUDED.account_id,page_id=EXCLUDED.page_id""",
                    (uid, request.form.get("account_id", "").strip(), request.form.get("page_id", "").strip()))
        banco.commit()
    finally:
        banco.close()
    return redirect("/configuracoes")


def registrar_mensagem(canal, identificador, nome, texto, direcao="recebida", externa_id=None):
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("""SELECT * FROM conversas WHERE canal=%s AND identificador=%s ORDER BY id DESC LIMIT 1""", (canal, identificador))
            conversa = cursor.fetchone()
            if not conversa:
                cursor.execute("SELECT id FROM usuarios ORDER BY id LIMIT 1")
                owner = cursor.fetchone()
                if not owner:
                    banco.rollback(); return None
                cursor.execute("""INSERT INTO conversas (usuario_id,canal,identificador,nome,ultima_mensagem) VALUES (%s,%s,%s,%s,%s) RETURNING *""", (owner["id"], canal, identificador, nome, texto))
                conversa = cursor.fetchone()
            else:
                cursor.execute("UPDATE conversas SET nome=COALESCE(%s,nome),ultima_mensagem=%s WHERE id=%s", (nome, texto, conversa["id"]))
            cursor.execute("INSERT INTO mensagens (conversa_id,direcao,texto,externa_id) VALUES (%s,%s,%s,%s)", (conversa["id"], direcao, texto, externa_id))
        banco.commit()
        return conversa["id"]
    finally:
        banco.close()


@app.route("/enviar-mensagem/<int:conversa_id>", methods=["POST"])
def enviar_mensagem(conversa_id):
    if "usuario" not in session:
        return redirect("/")
    texto = request.form.get("mensagem", "").strip()
    if not texto:
        return redirect(f"/atendimento?conversa={conversa_id}")
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,))
            conversa = cursor.fetchone()
        if not conversa:
            return redirect("/atendimento")
        if conversa["canal"] == "whatsapp":
            enviar_whatsapp(conversa["identificador"], texto)
        elif conversa["canal"] == "instagram":
            enviar_instagram(conversa["identificador"], texto)
        registrar_mensagem(conversa["canal"], conversa["identificador"], conversa["nome"], texto, "enviada")
    finally:
        banco.close()
    return redirect(f"/atendimento?conversa={conversa_id}")


def enviar_whatsapp(destino, texto):
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        return
    url = f"https://graph.facebook.com/v23.0/{phone_id}/messages"
    payload = json.dumps({"messaging_product":"whatsapp","to":destino,"type":"text","text":{"body":texto}}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=15).read()


def enviar_instagram(destino, texto):
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        return
    url = "https://graph.facebook.com/v23.0/me/messages"
    payload = json.dumps({"recipient":{"id":destino},"message":{"text":texto}}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=15).read()


@app.route("/webhook/whatsapp", methods=["GET", "POST"])
def webhook_whatsapp():
    if request.method == "GET":
        verify_token = os.environ.get("META_VERIFY_TOKEN", "seven-store-webhook")
        if request.args.get("hub.verify_token") == verify_token:
            return request.args.get("hub.challenge", ""), 200
        return "Token inválido", 403
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    if msg.get("type") == "text":
                        contato = msg.get("from", "desconhecido")
                        texto = msg.get("text", {}).get("body", "")
                        nome = None
                        contatos = value.get("contacts", [])
                        if contatos:
                            nome = contatos[0].get("profile", {}).get("name")
                        registrar_mensagem("whatsapp", contato, nome, texto, "recebida", msg.get("id"))
    except Exception:
        app.logger.exception("Erro no webhook WhatsApp")
    return "OK", 200


@app.route("/webhook/instagram", methods=["GET", "POST"])
def webhook_instagram():
    if request.method == "GET":
        verify_token = os.environ.get("META_VERIFY_TOKEN", "seven-store-webhook")
        if request.args.get("hub.verify_token") == verify_token:
            return request.args.get("hub.challenge", ""), 200
        return "Token inválido", 403
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for messaging in entry.get("messaging", []):
                sender = messaging.get("sender", {}).get("id")
                texto = messaging.get("message", {}).get("text")
                if sender and texto:
                    registrar_mensagem("instagram", sender, None, texto, "recebida", messaging.get("message", {}).get("mid"))
    except Exception:
        app.logger.exception("Erro no webhook Instagram")
    return "EVENT_RECEIVED", 200


@app.route("/sair")
def sair():
    session.clear()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
