from flask import Flask, render_template, request, redirect, session, jsonify, send_file
import os
import json
import urllib.request
import psycopg2
from psycopg2 import errors
from psycopg2.extras import RealDictCursor
from decimal import Decimal, InvalidOperation
from io import BytesIO
import qrcode

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "seven_store_versao_08")


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
                    senha VARCHAR(255) NOT NULL,
                    perfil VARCHAR(20) NOT NULL DEFAULT 'usuario'
                )
            """)
            cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perfil VARCHAR(20) NOT NULL DEFAULT 'usuario'")
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS caixas (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,
                    nome VARCHAR(100) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'aberto',
                    saldo_inicial NUMERIC(12,2) NOT NULL DEFAULT 0,
                    aberto_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    fechado_em TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS vendas (
                    id SERIAL PRIMARY KEY,
                    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
                    caixa_id INTEGER REFERENCES caixas(id) ON DELETE SET NULL,
                    total NUMERIC(12,2) NOT NULL,
                    pagamento VARCHAR(30) NOT NULL,
                    descricao VARCHAR(255),
                    pix_chave VARCHAR(255),
                    criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS configuracoes_loja (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    pix_chave VARCHAR(255),
                    pix_nome VARCHAR(100),
                    pix_cidade VARCHAR(100),
                    atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("INSERT INTO configuracoes_loja (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
            total = cursor.fetchone()["total"]
            if total == 0:
                pass
            cursor.execute("UPDATE usuarios SET perfil='administrador' WHERE perfil IS NULL OR perfil=''")
        banco.commit()
    finally:
        banco.close()


criar_banco()


def usuario_atual():
    if "usuario_id" not in session:
        return None
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE id=%s", (session["usuario_id"],))
            return cursor.fetchone()
    finally:
        banco.close()


def usuario_id_atual():
    atual = usuario_atual()
    return atual["id"] if atual else None


def exigir_login():
    return "usuario_id" in session


def exigir_perfil(*perfis):
    atual = usuario_atual()
    return atual and atual["perfil"] in perfis


def garantir_caixa(uid):
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM caixas WHERE usuario_id=%s", (uid,))
            caixa = cursor.fetchone()
            if not caixa:
                cursor.execute("INSERT INTO caixas (usuario_id,nome,status) VALUES (%s,%s,'aberto') RETURNING *", (uid, f"Caixa {uid}"))
                caixa = cursor.fetchone()
        banco.commit()
        return caixa
    finally:
        banco.close()


def contexto_base():
    atual = usuario_atual()
    return {
        "usuario": atual["usuario"] if atual else "",
        "perfil": atual["perfil"] if atual else "",
        "perfil_nome": {"administrador": "Administrador", "subadministrador": "Sub-administrador", "usuario": "Usuário"}.get(atual["perfil"] if atual else "", "")
    }


@app.route("/")
def login():
    if exigir_login():
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
        session["usuario_id"] = resultado["id"]
        session["usuario"] = resultado["usuario"]
        garantir_caixa(resultado["id"])
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
            cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
            primeiro = cursor.fetchone()["total"] == 0
            perfil = "administrador" if primeiro else "usuario"
            cursor.execute("INSERT INTO usuarios (usuario, senha, perfil) VALUES (%s,%s,%s)", (usuario, senha, perfil))
            cursor.execute("SELECT id FROM usuarios WHERE usuario=%s", (usuario,))
            uid = cursor.fetchone()["id"]
        banco.commit()
        garantir_caixa(uid)
    except errors.UniqueViolation:
        banco.rollback()
        return render_template("cadastro.html", erro="Esse usuário já existe.")
    finally:
        banco.close()
    return redirect("/")


@app.route("/inicio")
def inicio():
    if not exigir_login():
        return redirect("/")
    return render_template("inicio.html", **contexto_base())


def pagina_menu(titulo, descricao, *perfis):
    if not exigir_login():
        return redirect("/")
    if perfis and not exigir_perfil(*perfis):
        return redirect("/inicio")
    return render_template("pagina.html", titulo=titulo, descricao=descricao, **contexto_base())


@app.route("/produtos")
def produtos():
    return pagina_menu("Produtos", "Aqui você poderá cadastrar e gerenciar os produtos.", "administrador", "subadministrador")


@app.route("/clientes")
def clientes():
    return pagina_menu("Clientes", "Aqui você poderá cadastrar e gerenciar os clientes.", "administrador", "subadministrador")


@app.route("/estoque")
def estoque():
    return pagina_menu("Estoque", "Aqui você poderá controlar o estoque da loja.", "administrador", "subadministrador")


@app.route("/relatorios")
def relatorios():
    return pagina_menu("Relatórios", "Aqui você poderá consultar os relatórios do sistema.", "administrador", "subadministrador")


@app.route("/caixa")
def caixa():
    if not exigir_login():
        return redirect("/")
    uid = usuario_id_atual()
    meu_caixa = garantir_caixa(uid)
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(total),0) AS total FROM vendas WHERE caixa_id=%s", (meu_caixa["id"],))
            total = cursor.fetchone()["total"]
            cursor.execute("SELECT * FROM vendas WHERE caixa_id=%s ORDER BY criada_em DESC LIMIT 20", (meu_caixa["id"],))
            vendas_recentes = cursor.fetchall()
    finally:
        banco.close()
    return render_template("pagina.html", titulo=f"Caixa - {meu_caixa['nome']}", descricao=f"Status: {meu_caixa['status']} | Total vendido: R$ {Decimal(total or 0):.2f}", vendas=vendas_recentes, **contexto_base())


@app.route("/vendas", methods=["GET", "POST"])
def vendas():
    if not exigir_login():
        return redirect("/")
    uid = usuario_id_atual()
    caixa_atual = garantir_caixa(uid)
    mensagem = None
    erro = None
    if request.method == "POST":
        try:
            total = Decimal(request.form.get("total", "0").replace(",", "."))
            pagamento = request.form.get("pagamento", "dinheiro").strip()
            descricao = request.form.get("descricao", "").strip()
            if total <= 0:
                raise InvalidOperation
            banco = conectar_banco()
            try:
                with banco.cursor() as cursor:
                    cursor.execute("SELECT pix_chave FROM configuracoes_loja WHERE id=1")
                    loja = cursor.fetchone()
                    pix_chave = loja["pix_chave"] if loja else None
                    cursor.execute("INSERT INTO vendas (usuario_id,caixa_id,total,pagamento,descricao,pix_chave) VALUES (%s,%s,%s,%s,%s,%s)", (uid, caixa_atual["id"], total, pagamento, descricao, pix_chave if pagamento == "pix" else None))
                banco.commit()
            finally:
                banco.close()
            mensagem = f"Venda registrada: R$ {total:.2f}"
        except (InvalidOperation, ValueError):
            erro = "Informe um valor de venda válido."
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT pix_chave,pix_nome,pix_cidade FROM configuracoes_loja WHERE id=1")
            loja = cursor.fetchone() or {}
            cursor.execute("SELECT * FROM vendas WHERE usuario_id=%s ORDER BY criada_em DESC LIMIT 20", (uid,))
            vendas_recentes = cursor.fetchall()
    finally:
        banco.close()
    return render_template("vendas.html", **contexto_base(), caixa=caixa_atual, loja=loja, vendas=vendas_recentes, mensagem=mensagem, erro=erro)


@app.route("/pix/qr")
def pix_qr():
    if not exigir_login():
        return redirect("/")
    chave = request.args.get("chave", "").strip()
    valor = request.args.get("valor", "").strip()
    nome = request.args.get("nome", "Seven Store").strip()[:25]
    cidade = request.args.get("cidade", "BRASILIA").strip()[:15]
    if not chave:
        banco = conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("SELECT pix_chave,pix_nome,pix_cidade FROM configuracoes_loja WHERE id=1")
                loja = cursor.fetchone() or {}
        finally:
            banco.close()
        chave = (loja.get("pix_chave") or "").strip()
        nome = (loja.get("pix_nome") or nome).strip()[:25]
        cidade = (loja.get("pix_cidade") or cidade).strip()[:15]
    if not chave:
        return "Chave Pix não cadastrada.", 400
    payload = gerar_payload_pix(chave, valor, nome, cidade)
    imagem = qrcode.make(payload)
    arquivo = BytesIO()
    imagem.save(arquivo, format="PNG")
    arquivo.seek(0)
    return send_file(arquivo, mimetype="image/png")


def campo_pix(id_campo, valor):
    tamanho = f"{len(valor):02d}"
    return f"{id_campo}{tamanho}{valor}"


def gerar_payload_pix(chave, valor, nome, cidade):
    merchant = campo_pix("00", "BR.GOV.BCB.PIX") + campo_pix("01", chave)
    if valor:
        try:
            valor_decimal = Decimal(valor.replace(",", "."))
            valor_txt = f"{valor_decimal:.2f}"
        except Exception:
            valor_txt = ""
    else:
        valor_txt = ""
    payload = campo_pix("00", "01")
    payload += campo_pix("26", merchant)
    payload += campo_pix("52", "0000")
    payload += campo_pix("53", "986")
    if valor_txt:
        payload += campo_pix("54", valor_txt)
    payload += campo_pix("58", "BR")
    payload += campo_pix("59", nome or "SEVEN STORE")
    payload += campo_pix("60", cidade or "BRASILIA")
    payload += campo_pix("62", campo_pix("05", "SEVENSTORE"))
    base = payload + "6304"
    crc = 0xFFFF
    for byte in base.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return base + f"{crc:04X}"


@app.route("/atendimento")
def atendimento():
    if not exigir_login():
        return redirect("/")
    uid = usuario_id_atual()
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM conversas WHERE usuario_id=%s ORDER BY id DESC", (uid,))
            conversas = cursor.fetchall()
            conversa_id = request.args.get("conversa", type=int)
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
    return render_template("atendimento.html", **contexto_base(), conversas=conversas, conversa_atual=atual, mensagens=mensagens, conversa_selecionada=conversa_id,
        whatsapp_status="Conectado" if os.environ.get("WHATSAPP_ACCESS_TOKEN") and "whatsapp" in integracoes else "Aguardando configuração",
        instagram_status="Conectado" if os.environ.get("INSTAGRAM_ACCESS_TOKEN") and "instagram" in integracoes else "Aguardando configuração")


@app.route("/configuracoes")
def configuracoes():
    if not exigir_perfil("administrador"):
        return redirect("/inicio")
    uid = usuario_id_atual()
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM integracoes WHERE usuario_id=%s", (uid,))
            dados = {r["canal"]: r for r in cursor.fetchall()}
            cursor.execute("SELECT * FROM usuarios ORDER BY id")
            usuarios = cursor.fetchall()
            cursor.execute("SELECT * FROM configuracoes_loja WHERE id=1")
            loja = cursor.fetchone() or {}
    finally:
        banco.close()
    return render_template("configuracoes.html", **contexto_base(), usuarios=usuarios, loja=loja,
        whatsapp=dados.get("whatsapp", {}), instagram=dados.get("instagram", {}),
        whatsapp_configurado="whatsapp" in dados, instagram_configurado="instagram" in dados)


@app.route("/configuracoes/usuario/<int:usuario_id>/perfil", methods=["POST"])
def alterar_perfil(usuario_id):
    if not exigir_perfil("administrador"):
        return redirect("/inicio")
    perfil = request.form.get("perfil", "usuario")
    if perfil not in ("administrador", "subadministrador", "usuario"):
        return redirect("/configuracoes")
    if usuario_id == usuario_id_atual() and perfil != "administrador":
        return redirect("/configuracoes")
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("UPDATE usuarios SET perfil=%s WHERE id=%s", (perfil, usuario_id))
        banco.commit()
    finally:
        banco.close()
    return redirect("/configuracoes")


@app.route("/configuracoes/pix", methods=["POST"])
def salvar_pix():
    if not exigir_perfil("administrador"):
        return redirect("/inicio")
    chave = request.form.get("pix_chave", "").strip()
    nome = request.form.get("pix_nome", "Seven Store").strip()
    cidade = request.form.get("pix_cidade", "BRASILIA").strip()
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("UPDATE configuracoes_loja SET pix_chave=%s,pix_nome=%s,pix_cidade=%s,atualizado_em=CURRENT_TIMESTAMP WHERE id=1", (chave, nome, cidade))
        banco.commit()
    finally:
        banco.close()
    return redirect("/configuracoes")


@app.route("/configuracoes/salvar", methods=["POST"])
def salvar_configuracao():
    if not exigir_perfil("administrador"):
        return redirect("/inicio")
    uid = usuario_id_atual()
    canal = request.form.get("canal")
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            if canal == "whatsapp":
                cursor.execute("""INSERT INTO integracoes (usuario_id,canal,phone_number_id,business_account_id) VALUES (%s,'whatsapp',%s,%s) ON CONFLICT (usuario_id,canal) DO UPDATE SET phone_number_id=EXCLUDED.phone_number_id,business_account_id=EXCLUDED.business_account_id""", (uid, request.form.get("phone_number_id", "").strip(), request.form.get("business_account_id", "").strip()))
            elif canal == "instagram":
                cursor.execute("""INSERT INTO integracoes (usuario_id,canal,account_id,page_id) VALUES (%s,'instagram',%s,%s) ON CONFLICT (usuario_id,canal) DO UPDATE SET account_id=EXCLUDED.account_id,page_id=EXCLUDED.page_id""", (uid, request.form.get("account_id", "").strip(), request.form.get("page_id", "").strip()))
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
                    banco.rollback()
                    return None
                cursor.execute("INSERT INTO conversas (usuario_id,canal,identificador,nome,ultima_mensagem) VALUES (%s,%s,%s,%s,%s) RETURNING *", (owner["id"], canal, identificador, nome, texto))
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
    if not exigir_login():
        return redirect("/")
    texto = request.form.get("mensagem", "").strip()
    if not texto:
        return redirect(f"/atendimento?conversa={conversa_id}")
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM conversas WHERE id=%s AND usuario_id=%s", (conversa_id, usuario_id_atual()))
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
