from flask import request, jsonify, render_template, redirect, url_for
from decimal import Decimal, InvalidOperation
from datetime import datetime
import os
import json
from urllib.request import Request, urlopen


def instalar(app):
    if getattr(app, "_pending_extension_instalada", False):
        return
    app._pending_extension_instalada = True

    def banco():
        return app.conectar_banco()

    def uid():
        atual = app.usuario_atual()
        return atual["id"] if atual else None

    def autorizado():
        return uid() is not None

    def perfil_admin():
        atual = app.usuario_atual()
        return bool(atual and atual["perfil"] in ("administrador", "subadministrador"))

    def garantir_tabelas():
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""CREATE TABLE IF NOT EXISTS vendas_pendentes (
                    id SERIAL PRIMARY KEY, usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
                    caixa_id INTEGER REFERENCES caixas(id) ON DELETE CASCADE, total NUMERIC(12,2) NOT NULL,
                    pagamento VARCHAR(30) NOT NULL DEFAULT 'dinheiro', descricao VARCHAR(255),
                    criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, atualizada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_vendas_pendentes_caixa ON vendas_pendentes(caixa_id, criada_em DESC)")
                cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS access_token TEXT")
                cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS verify_token VARCHAR(255)")
                cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT FALSE")
            conexao.commit()
        finally:
            conexao.close()

    garantir_tabelas()

    @app.route("/api/vendas-pendentes", methods=["GET"])
    def listar_vendas_pendentes_compartilhadas():
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        caixa = app.garantir_caixa(uid())
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""SELECT vp.id,vp.total,vp.pagamento,vp.descricao,vp.criada_em,u.usuario,c.nome AS caixa
                    FROM vendas_pendentes vp LEFT JOIN usuarios u ON u.id=vp.usuario_id LEFT JOIN caixas c ON c.id=vp.caixa_id
                    WHERE vp.caixa_id=%s ORDER BY vp.criada_em DESC""", (caixa["id"],))
                rows = cursor.fetchall()
        finally:
            conexao.close()
        resultado = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("total"), Decimal):
                item["total"] = float(item["total"])
            if isinstance(item.get("criada_em"), datetime):
                item["criada_em"] = item["criada_em"].strftime("%d/%m/%Y %H:%M")
            resultado.append(item)
        return jsonify({"pendentes": resultado})

    @app.route("/api/vendas-pendentes", methods=["POST"])
    def criar_venda_pendente_compartilhada():
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        dados = request.get_json(silent=True) or request.form
        try:
            total = Decimal(str(dados.get("total", "0")).replace(",", "."))
            if total <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            return jsonify({"erro": "Informe um valor válido."}), 400
        pagamento = str(dados.get("pagamento", "dinheiro")).strip() or "dinheiro"
        descricao = str(dados.get("descricao", "")).strip()[:255]
        caixa = app.garantir_caixa(uid())
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""INSERT INTO vendas_pendentes(usuario_id,caixa_id,total,pagamento,descricao)
                    VALUES(%s,%s,%s,%s,%s) RETURNING id,total,pagamento,descricao,criada_em""",
                    (uid(), caixa["id"], total, pagamento, descricao))
                row = cursor.fetchone()
            conexao.commit()
        finally:
            conexao.close()
        item = dict(row)
        item["total"] = float(item["total"])
        if isinstance(item.get("criada_em"), datetime):
            item["criada_em"] = item["criada_em"].strftime("%d/%m/%Y %H:%M")
        return jsonify({"ok": True, "pendente": item}), 201

    @app.route("/api/vendas-pendentes/<int:pendente_id>/continuar", methods=["POST"])
    def continuar_venda_pendente_compartilhada(pendente_id):
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        caixa = app.garantir_caixa(uid())
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT id,total,pagamento,descricao FROM vendas_pendentes WHERE id=%s AND caixa_id=%s", (pendente_id, caixa["id"]))
                row = cursor.fetchone()
                if not row:
                    return jsonify({"erro": "Venda pendente não encontrada neste caixa."}), 404
                cursor.execute("DELETE FROM vendas_pendentes WHERE id=%s AND caixa_id=%s", (pendente_id, caixa["id"]))
            conexao.commit()
        finally:
            conexao.close()
        item = dict(row)
        item["total"] = float(item["total"])
        return jsonify({"ok": True, "pendente": item})

    @app.route("/api/vendas-pendentes/<int:pendente_id>", methods=["DELETE"])
    def excluir_venda_pendente_compartilhada(pendente_id):
        if not autorizado():
            return jsonify({"erro": "Faça login novamente."}), 401
        caixa = app.garantir_caixa(uid())
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("DELETE FROM vendas_pendentes WHERE id=%s AND caixa_id=%s", (pendente_id, caixa["id"]))
                apagadas = cursor.rowcount
            conexao.commit()
        finally:
            conexao.close()
        if not apagadas:
            return jsonify({"erro": "Venda pendente não encontrada neste caixa."}), 404
        return jsonify({"ok": True})

    def buscar_conversas(conexao):
        with conexao.cursor() as cursor:
            cursor.execute("SELECT id,canal,identificador,nome,status,ultima_mensagem FROM conversas ORDER BY id DESC")
            return cursor.fetchall()

    def atendimento_view():
        # Todos os perfis logados podem consultar e usar o Atendimento.
        if not autorizado():
            return redirect("/")
        conversa_id = request.args.get("conversa", type=int)
        conexao = banco()
        try:
            conversas = buscar_conversas(conexao)
            atual = None
            mensagens = []
            with conexao.cursor() as cursor:
                if conversa_id:
                    cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,))
                    atual = cursor.fetchone()
                    if atual:
                        cursor.execute("SELECT * FROM mensagens WHERE conversa_id=%s ORDER BY criada_em", (conversa_id,))
                        mensagens = cursor.fetchall()
                cursor.execute("SELECT canal,account_id,page_id,phone_number_id,business_account_id,ativo FROM integracoes ORDER BY canal")
                integracoes = cursor.fetchall()
        finally:
            conexao.close()
        estados = {"whatsapp": "Não conectado", "instagram": "Não conectado"}
        for i in integracoes:
            if i["canal"] in estados and i["ativo"]:
                estados[i["canal"]] = "Conectado"
        return render_template("atendimento.html", **app.contexto_base(),
                               conversas=conversas, conversa_atual=atual, mensagens=mensagens,
                               conversa_selecionada=conversa_id,
                               whatsapp_status=estados["whatsapp"],
                               instagram_status=estados["instagram"], integracoes=integracoes)

    app.view_functions["atendimento"] = atendimento_view

    @app.route("/configuracoes/integracao", methods=["POST"])
    def salvar_integracao():
        if not autorizado() or not app.exigir_perfil("administrador"):
            return redirect("/inicio")
        canal = request.form.get("canal", "").strip().lower()
        if canal not in ("whatsapp", "instagram"):
            return redirect("/configuracoes")
        account_id = request.form.get("account_id", "").strip()
        page_id = request.form.get("page_id", "").strip()
        phone_id = request.form.get("phone_number_id", "").strip()
        business_id = request.form.get("business_account_id", "").strip()
        token = request.form.get("access_token", "").strip()
        verify = request.form.get("verify_token", "").strip()
        ativo = bool(token)
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""INSERT INTO integracoes(usuario_id,canal,account_id,page_id,phone_number_id,business_account_id,access_token,verify_token,ativo)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(usuario_id,canal) DO UPDATE SET account_id=EXCLUDED.account_id,page_id=EXCLUDED.page_id,
                    phone_number_id=EXCLUDED.phone_number_id,business_account_id=EXCLUDED.business_account_id,
                    access_token=EXCLUDED.access_token,verify_token=EXCLUDED.verify_token,ativo=EXCLUDED.ativo""",
                    (uid(), canal, account_id, page_id, phone_id, business_id, token, verify, ativo))
            conexao.commit()
        finally:
            conexao.close()
        return redirect("/configuracoes")

    @app.route("/atendimento/mensagem/<int:conversa_id>", methods=["POST"])
    def enviar_mensagem(conversa_id):
        # Todos os perfis logados podem responder no Atendimento.
        if not autorizado():
            return redirect("/inicio")
        texto = request.form.get("mensagem", "").strip()
        if not texto:
            return redirect(url_for("atendimento", conversa=conversa_id))
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,))
                conversa = cursor.fetchone()
                if not conversa:
                    return redirect("/atendimento")
                cursor.execute("INSERT INTO mensagens(conversa_id,direcao,texto) VALUES(%s,'enviada',%s)", (conversa_id, texto))
                cursor.execute("UPDATE conversas SET ultima_mensagem=%s WHERE id=%s", (texto, conversa_id))
            conexao.commit()
        finally:
            conexao.close()
        try:
            enviar_meta(conversa["canal"], conversa["identificador"], texto)
        except Exception:
            pass
        return redirect(url_for("atendimento", conversa=conversa_id))

    def meta_integracao(canal):
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT * FROM integracoes WHERE canal=%s AND ativo=TRUE ORDER BY usuario_id LIMIT 1", (canal,))
                return cursor.fetchone()
        finally:
            conexao.close()

    def enviar_meta(canal, recipient, texto):
        integ = meta_integracao(canal)
        if not integ or not integ["access_token"]:
            return False
        versao = os.environ.get("META_GRAPH_VERSION", "v23.0")
        if canal == "whatsapp":
            phone = integ["phone_number_id"]
            if not phone:
                return False
            endpoint = f"https://graph.facebook.com/{versao}/{phone}/messages"
            body = {"messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"body": texto}}
        else:
            endpoint = f"https://graph.facebook.com/{versao}/me/messages"
            body = {"recipient": {"id": recipient}, "message": {"text": texto}}
        req = Request(endpoint, data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + integ["access_token"], "Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=15) as response:
            return response.status in (200, 201)

    def salvar_mensagem_webhook(canal, identificador, nome, texto, externa_id=None):
        conexao = banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT id FROM usuarios WHERE perfil='administrador' ORDER BY id LIMIT 1")
                owner = cursor.fetchone()
                if not owner:
                    return
                cursor.execute("SELECT id FROM conversas WHERE canal=%s AND identificador=%s", (canal, identificador))
                conv = cursor.fetchone()
                if conv:
                    cid = conv["id"]
                else:
                    cursor.execute("INSERT INTO conversas(usuario_id,canal,identificador,nome,ultima_mensagem) VALUES(%s,%s,%s,%s,%s) RETURNING id", (owner["id"], canal, identificador, nome, texto))
                    cid = cursor.fetchone()["id"]
                cursor.execute("INSERT INTO mensagens(conversa_id,direcao,texto,externa_id) VALUES(%s,'recebida',%s,%s)", (cid, texto, externa_id))
                cursor.execute("UPDATE conversas SET nome=COALESCE(%s,nome),ultima_mensagem=%s WHERE id=%s", (nome, texto, cid))
            conexao.commit()
        finally:
            conexao.close()

    @app.route("/webhook/meta", methods=["GET", "POST"])
    def webhook_meta():
        if request.method == "GET":
            mode = request.args.get("hub.mode")
            token = request.args.get("hub.verify_token")
            challenge = request.args.get("hub.challenge")
            if mode == "subscribe":
                conexao = banco()
                try:
                    with conexao.cursor() as cursor:
                        cursor.execute("SELECT verify_token FROM integracoes WHERE verify_token=%s AND ativo=TRUE LIMIT 1", (token,))
                        ok = cursor.fetchone()
                finally:
                    conexao.close()
                if ok:
                    return challenge or "", 200
            return "Verificação inválida", 403
        data = request.get_json(silent=True) or {}
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                canal = "whatsapp" if "messages" in value else "instagram"
                for msg in value.get("messages", []):
                    texto = (msg.get("text") or {}).get("body") or msg.get("button", {}).get("text") or "Mensagem recebida"
                    salvar_mensagem_webhook(canal, msg.get("from") or msg.get("sender", {}).get("id", ""), None, texto, msg.get("id"))
                for msg in value.get("messaging", []):
                    texto = ((msg.get("message") or {}).get("text"))
                    if texto:
                        salvar_mensagem_webhook("instagram", msg.get("sender", {}).get("id", ""), None, texto, msg.get("message", {}).get("mid"))
        return "EVENT_RECEIVED", 200
