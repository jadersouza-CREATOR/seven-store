from flask import Flask, render_template, request, redirect, session, url_for
import os
import json
import urllib.request
import urllib.parse
import secrets
import psycopg2
from psycopg2 import errors
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "seven_store_versao_08")

META_GRAPH_VERSION = os.environ.get("META_GRAPH_VERSION", "v23.0")
META_AUTH_URL = f"https://www.facebook.com/{META_GRAPH_VERSION}/dialog/oauth"
META_GRAPH_URL = f"https://graph.facebook.com/{META_GRAPH_VERSION}"


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
                    access_token TEXT,
                    nome_conta VARCHAR(200),
                    UNIQUE(usuario_id, canal)
                )
            """)
            cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS access_token TEXT")
            cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS nome_conta VARCHAR(200)")
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


def meta_configurada():
    return bool(os.environ.get("META_APP_ID") and os.environ.get("META_APP_SECRET"))


def meta_redirect_uri():
    return os.environ.get("META_REDIRECT_URI") or url_for("meta_callback", _external=True)


def salvar_integracao(canal, access_token, account_id=None, page_id=None, phone_number_id=None, business_account_id=None, nome_conta=None):
    uid = usuario_id_atual()
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("""
                INSERT INTO integracoes
                (usuario_id, canal, account_id, page_id, phone_number_id, business_account_id, access_token, nome_conta)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (usuario_id,canal) DO UPDATE SET
                    account_id=COALESCE(EXCLUDED.account_id,integracoes.account_id),
                    page_id=COALESCE(EXCLUDED.page_id,integracoes.page_id),
                    phone_number_id=COALESCE(EXCLUDED.phone_number_id,integracoes.phone_number_id),
                    business_account_id=COALESCE(EXCLUDED.business_account_id,integracoes.business_account_id),
                    access_token=EXCLUDED.access_token,
                    nome_conta=COALESCE(EXCLUDED.nome_conta,integracoes.nome_conta)
            """, (uid, canal, account_id, page_id, phone_number_id, business_account_id, access_token, nome_conta))
        banco.commit()
    finally:
        banco.close()


def buscar_integracao(canal, usuario_id=None):
    uid = usuario_id or usuario_id_atual()
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM integracoes WHERE usuario_id=%s AND canal=%s", (uid, canal))
            return cursor.fetchone()
    finally:
        banco.close()


def meta_access_token(code):
    params = urllib.parse.urlencode({
        "client_id": os.environ["META_APP_ID"],
        "client_secret": os.environ["META_APP_SECRET"],
        "redirect_uri": meta_redirect_uri(),
        "code": code
    })
    with urllib.request.urlopen(f"{META_GRAPH_URL}/oauth/access_token?{params}", timeout=20) as response:
        data = json.loads(response.read().decode())
    return data["access_token"]


def meta_get(path, token, params=None):
    query = {"access_token": token}
    if params:
        query.update(params)
    url = f"{META_GRAPH_URL}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode())


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
    return render_template("atendimento.html", usuario=session["usuario"], conversas=conversas,
        conversa_atual=atual, mensagens=mensagens, conversa_selecionada=conversa_id,
        whatsapp_status="Conectado" if "whatsapp" in integracoes and integracoes["whatsapp"].get("access_token") else "Aguardando configuração",
        instagram_status="Conectado" if "instagram" in integracoes and integracoes["instagram"].get("access_token") else "Aguardando configuração")


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
        whatsapp_configurado="whatsapp" in dados and bool(dados["whatsapp"].get("access_token")),
        instagram_configurado="instagram" in dados and bool(dados["instagram"].get("access_token")),
        meta_configurada=meta_configurada(), meta_redirect_uri=meta_redirect_uri())


@app.route("/meta/login/<canal>")
def meta_login(canal):
    if "usuario" not in session:
        return redirect("/")
    if canal not in ("instagram", "whatsapp"):
        return "Canal inválido", 400
    if not meta_configurada():
        return redirect(url_for("configuracoes", erro="Configure META_APP_ID e META_APP_SECRET no Render primeiro."))

    state = secrets.token_urlsafe(32)
    session["meta_oauth_state"] = state
    session["meta_oauth_canal"] = canal

    if canal == "instagram":
        scope = "instagram_basic,instagram_manage_messages,pages_show_list,pages_read_engagement"
    else:
        scope = "business_management,whatsapp_business_management,whatsapp_business_messaging"

    params = {
        "client_id": os.environ["META_APP_ID"],
        "redirect_uri": meta_redirect_uri(),
        "state": state,
        "response_type": "code",
        "scope": scope
    }
    return redirect(f"{META_AUTH_URL}?{urllib.parse.urlencode(params)}")


@app.route("/meta/callback")
def meta_callback():
    if "usuario" not in session:
        return redirect("/")
    if request.args.get("error"):
        return redirect(url_for("configuracoes", erro="A autorização da Meta foi cancelada."))
    state = request.args.get("state")
    if not state or not secrets.compare_digest(state, session.pop("meta_oauth_state", "")):
        return "Estado OAuth inválido", 400

    canal = session.pop("meta_oauth_canal", None)
    code = request.args.get("code")
    if canal not in ("instagram", "whatsapp") or not code:
        return redirect(url_for("configuracoes", erro="Resposta da Meta inválida."))

    try:
        token = meta_access_token(code)
        if canal == "instagram":
            pages = meta_get("me/accounts", token, {"fields": "id,name,access_token,instagram_business_account"})
            page = next((p for p in pages.get("data", []) if p.get("instagram_business_account")), None)
            if not page:
                return redirect(url_for("configuracoes", erro="Nenhuma Página com Instagram profissional foi encontrada na conta Meta."))
            ig = page.get("instagram_business_account", {})
            page_token = page.get("access_token") or token
            save_token = page_token
            save_account = str(ig.get("id", ""))
            save_page = str(page.get("id", ""))
            save_name = page.get("name")
            salvar_integracao("instagram", save_token, account_id=save_account, page_id=save_page, nome_conta=save_name)
        else:
            businesses = meta_get("me/businesses", token, {"fields": "id,name"})
            business = businesses.get("data", [None])[0]
            if not business:
                return redirect(url_for("configuracoes", erro="Nenhuma conta empresarial foi encontrada na Meta."))
            waba = meta_get(f"{business['id']}/owned_whatsapp_business_accounts", token, {"fields": "id,name"})
            account = waba.get("data", [None])[0]
            if not account:
                return redirect(url_for("configuracoes", erro="Nenhuma conta WhatsApp Business foi encontrada."))
            phones = meta_get(f"{account['id']}/phone_numbers", token, {"fields": "id,display_phone_number,verified_name"})
            phone = phones.get("data", [None])[0]
            salvar_integracao("whatsapp", token, phone_number_id=phone.get("id") if phone else None,
                business_account_id=str(account["id"]), nome_conta=account.get("name"))
    except Exception:
        app.logger.exception("Erro na conexão OAuth da Meta")
        return redirect(url_for("configuracoes", erro="Não foi possível concluir a conexão com a Meta. Confira o App ID, App Secret e as permissões."))

    return redirect(url_for("configuracoes", sucesso=f"{canal.capitalize()} conectado com sucesso."))


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
            cursor.execute("SELECT * FROM conversas WHERE canal=%s AND identificador=%s ORDER BY id DESC LIMIT 1", (canal, identificador))
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
    integracao = buscar_integracao("whatsapp")
    token = integracao.get("access_token") if integracao else None
    phone_id = integracao.get("phone_number_id") if integracao else None
    if not token or not phone_id:
        token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
        phone_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        return
    url = f"{META_GRAPH_URL}/{phone_id}/messages"
    payload = json.dumps({"messaging_product":"whatsapp","to":destino,"type":"text","text":{"body":texto}}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=15).read()


def enviar_instagram(destino, texto):
    integracao = buscar_integracao("instagram")
    token = integracao.get("access_token") if integracao else None
    if not token:
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        return
    url = f"{META_GRAPH_URL}/me/messages"
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
