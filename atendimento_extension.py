from flask import render_template, request, redirect, url_for, jsonify
from urllib.request import Request, urlopen
import json, os

def instalar(mod):
    app = mod.app
    if getattr(app, '_atendimento_v06_ok', False): return
    app._atendimento_v06_ok = True

    conexao = mod.conectar_banco()
    try:
        with conexao.cursor() as cursor:
            cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS access_token TEXT")
            cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS verify_token VARCHAR(255)")
            cursor.execute("ALTER TABLE integracoes ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT FALSE")
        conexao.commit()
    finally:
        conexao.close()

    def atendimento():
        if not mod.exigir_login(): return redirect('/')
        conexao = mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT id,canal,identificador,nome,status,ultima_mensagem FROM conversas ORDER BY id DESC")
                conversas = cursor.fetchall()
                conversa_id = request.args.get('conversa', type=int)
                atual = None; mensagens = []
                if conversa_id:
                    cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,)); atual = cursor.fetchone()
                    if atual:
                        cursor.execute("SELECT * FROM mensagens WHERE conversa_id=%s ORDER BY criada_em", (conversa_id,)); mensagens = cursor.fetchall()
                cursor.execute("SELECT canal,ativo FROM integracoes ORDER BY canal")
                integracoes = cursor.fetchall()
        finally: conexao.close()
        estados = {'whatsapp': 'Offline', 'instagram': 'Offline'}
        for item in integracoes:
            if item['canal'] in estados and item['ativo']: estados[item['canal']] = 'Conectado'
        return render_template('atendimento.html', **mod.contexto_base(), conversas=conversas,
            conversa_atual=atual, mensagens=mensagens, conversa_selecionada=conversa_id,
            whatsapp_status=estados['whatsapp'], instagram_status=estados['instagram'])

    def enviar(conversa_id):
        if not mod.exigir_login(): return redirect('/')
        texto = request.form.get('mensagem', '').strip()
        if not texto: return redirect(url_for('atendimento', conversa=conversa_id))
        conexao = mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT * FROM conversas WHERE id=%s", (conversa_id,)); conv = cursor.fetchone()
                if not conv: return redirect('/atendimento')
                cursor.execute("INSERT INTO mensagens(conversa_id,direcao,texto) VALUES(%s,'enviada',%s)", (conversa_id, texto))
                cursor.execute("UPDATE conversas SET ultima_mensagem=%s WHERE id=%s", (texto, conversa_id))
            conexao.commit()
        finally: conexao.close()
        try: enviar_meta(conv['canal'], conv['identificador'], texto)
        except Exception: pass
        return redirect(url_for('atendimento', conversa=conversa_id))

    def salvar_integracao():
        if not mod.exigir_login() or not mod.exigir_perfil('administrador'): return redirect('/inicio')
        canal = request.form.get('canal','').strip().lower()
        if canal not in ('whatsapp','instagram'): return redirect('/configuracoes')
        account_id=request.form.get('account_id','').strip(); page_id=request.form.get('page_id','').strip()
        phone_id=request.form.get('phone_number_id','').strip(); business_id=request.form.get('business_account_id','').strip()
        token=request.form.get('access_token','').strip(); verify=request.form.get('verify_token','').strip()
        conexao=mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("""INSERT INTO integracoes(usuario_id,canal,account_id,page_id,phone_number_id,business_account_id,access_token,verify_token,ativo)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(usuario_id,canal) DO UPDATE SET account_id=EXCLUDED.account_id,page_id=EXCLUDED.page_id,
                    phone_number_id=EXCLUDED.phone_number_id,business_account_id=EXCLUDED.business_account_id,
                    access_token=EXCLUDED.access_token,verify_token=EXCLUDED.verify_token,ativo=EXCLUDED.ativo""",
                    (mod.usuario_id_atual(),canal,account_id,page_id,phone_id,business_id,token,verify,bool(token)))
            conexao.commit()
        finally: conexao.close()
        return redirect('/configuracoes')

    def meta_config(canal):
        conexao=mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT * FROM integracoes WHERE canal=%s AND ativo=TRUE ORDER BY usuario_id LIMIT 1",(canal,)); return cursor.fetchone()
        finally: conexao.close()

    def enviar_meta(canal, destinatario, texto):
        cfg=meta_config(canal)
        if not cfg or not cfg['access_token']: return False
        versao=os.environ.get('META_GRAPH_VERSION','v23.0')
        if canal=='whatsapp':
            if not cfg['phone_number_id']: return False
            endpoint=f"https://graph.facebook.com/{versao}/{cfg['phone_number_id']}/messages"
            body={'messaging_product':'whatsapp','to':destinatario,'type':'text','text':{'body':texto}}
        else:
            endpoint=f"https://graph.facebook.com/{versao}/me/messages"
            body={'recipient':{'id':destinatario},'message':{'text':texto}}
        req=Request(endpoint,data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+cfg['access_token'],'Content-Type':'application/json'},method='POST')
        with urlopen(req,timeout=15) as response: return response.status in (200,201)

    def webhook_meta():
        if request.method=='GET':
            token=request.args.get('hub.verify_token'); challenge=request.args.get('hub.challenge')
            conexao=mod.conectar_banco()
            try:
                with conexao.cursor() as cursor:
                    cursor.execute("SELECT id FROM integracoes WHERE verify_token=%s AND ativo=TRUE LIMIT 1",(token,)); ok=cursor.fetchone()
            finally: conexao.close()
            return (challenge,200) if ok else ('Verificação inválida',403)
        data=request.get_json(silent=True) or {}
        for entry in data.get('entry',[]):
            for change in entry.get('changes',[]):
                value=change.get('value',{})
                for msg in value.get('messages',[]):
                    texto=(msg.get('text') or {}).get('body') or 'Mensagem recebida'
                    salvar_recebida('whatsapp',msg.get('from',''),texto,msg.get('id'))
            for msg in entry.get('messaging',[]):
                texto=(msg.get('message') or {}).get('text')
                if texto: salvar_recebida('instagram',msg.get('sender',{}).get('id',''),texto,msg.get('message',{}).get('mid'))
        return 'EVENT_RECEIVED',200

    def salvar_recebida(canal, identificador, texto, externa_id=None):
        if not identificador: return
        conexao=mod.conectar_banco()
        try:
            with conexao.cursor() as cursor:
                cursor.execute("SELECT id FROM usuarios WHERE perfil='administrador' ORDER BY id LIMIT 1"); dono=cursor.fetchone()
                if not dono: return
                cursor.execute("SELECT id FROM conversas WHERE canal=%s AND identificador=%s",(canal,identificador)); conv=cursor.fetchone()
                if conv: cid=conv['id']
                else:
                    cursor.execute("INSERT INTO conversas(usuario_id,canal,identificador,nome,ultima_mensagem) VALUES(%s,%s,%s,%s,%s) RETURNING id",(dono['id'],canal,identificador,identificador,texto)); cid=cursor.fetchone()['id']
                cursor.execute("INSERT INTO mensagens(conversa_id,direcao,texto,externa_id) VALUES(%s,'recebida',%s,%s)",(cid,texto,externa_id))
                cursor.execute("UPDATE conversas SET ultima_mensagem=%s WHERE id=%s",(texto,cid))
            conexao.commit()
        finally: conexao.close()

    app.view_functions['atendimento']=atendimento
    app.add_url_rule('/atendimento/mensagem/<int:conversa_id>', 'enviar_mensagem', enviar, methods=['POST'])
    app.add_url_rule('/configuracoes/integracao', 'salvar_integracao', salvar_integracao, methods=['POST'])
    app.add_url_rule('/webhook/meta', 'webhook_meta', webhook_meta, methods=['GET','POST'])
