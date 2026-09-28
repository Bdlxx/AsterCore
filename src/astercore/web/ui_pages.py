"""Windows 端（栖星 AsterCore）面板页面片段

与 Linux 端**同一套界面**：骨架/样式/交互在共用的 panel.js + panel.css，
配置页与日志页的标记在共用的 panel_pages.py，本文件只写"桌面端特有的页面"：
  · 概览（当前账号 + 插件概览 + 运行环境）
  · 账号（多账号列表 + 新建账号）
  · 运行方式 / 安全设置
页面内容一律由后端生成，前端只挂载 + 事件委托（data-*）。
"""

import html as _html
import time

from astercore.web import panel_pages as pp      # 两端共用的页面/小件（同一份字节）
from astercore.web.panel_pages import esc, fmt_uptime, card, empty_state, status_tag


# ---------------------------------------------------------------- 概览
def _dashboard(ctx):
    acc = ctx.get('account') or {}
    info = ctx.get('info') or {}
    plugins = ctx.get('plugins') or []
    running = bool(acc.get('running'))
    aid = esc(acc.get('account_id') or '')

    if not aid:
        return empty_state('🤖', '还没有账号',
                           '先在左侧「账号」里新建一个账号，再回到概览看运行状态。')
    chips = ''.join(
        f'<div class="chip-btn" role="button" tabindex="0" data-open="config" data-key="{esc(p.get("name"))}">'
        f'<span style="font-size:16px">🧩</span>'
        f'<span style="font-size:13px;font-weight:500">{esc(p.get("display") or p.get("name_cn") or p.get("name"))}</span>'
        f'<span style="font-size:11px;color:var(--text-secondary)">{esc(p.get("kind") or "")}</span>'
        + ('' if p.get('enabled', True) else '<span class="el-tag warning">已停用</span>')
        + '</div>' for p in plugins) or \
        '<div style="padding:20px;color:var(--text-secondary);text-align:center">账号未运行，或没有插件</div>'

    return f"""
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-icon blue">🤖</div><div class="stat-info">
          <div class="label">账号状态</div>
          <div class="value" id="dashBotVal">{'运行中' if running else '已停止'}</div>
          <div class="desc" id="dashBotDesc">{esc(acc.get('display_name') or aid)} · {esc(acc.get('backend_name') or '')}</div>
        </div></div>
        <div class="stat-card"><div class="stat-icon green">🔗</div><div class="stat-info">
          <div class="label">后端连接</div>
          <div class="value">{'已连接' if acc.get('connected') else ('未连接' if running else '未启动')}</div>
          <div class="desc">{esc(acc.get('backend') or acc.get('backend_name') or '')}</div>
        </div></div>
        <div class="stat-card"><div class="stat-icon orange">🧩</div><div class="stat-info">
          <div class="label">已加载插件</div><div class="value">{len(plugins)} 个</div>
          <div class="desc">点击下方插件可直接配置</div>
        </div></div>
        <div class="stat-card"><div class="stat-icon purple">📦</div><div class="stat-info">
          <div class="label">版本</div><div class="value">v{esc(info.get('version') or '')}</div>
          <div class="desc">{'冻结版' if info.get('frozen') else '源码运行'}</div>
        </div></div>
      </div>
      <div class="card"><div class="card-header"><h3>🧩 插件概览</h3>
        <span style="font-size:12px;color:var(--text-secondary)">点击进入对应插件配置</span></div>
        <div class="card-body"><div style="display:flex;flex-wrap:wrap;gap:10px;padding:4px 0">
          {chips}</div>
          <div style="margin-top:14px;display:flex;gap:10px;flex-wrap:wrap">
            <button class="btn btn-primary" data-act="start" data-key="{aid}">▶ 启动账号</button>
            <button class="btn btn-default" data-act="stop" data-key="{aid}">⏹ 停止账号</button>
            <button class="btn btn-default" data-act="reload-plugins" data-key="{aid}">🔄 重载插件</button>
          </div>
        </div></div>
      <div class="card"><div class="card-header"><h3>🔍 运行环境</h3></div><div class="card-body">
        {pp.info_table([
            ('程序版本', f"v{esc(info.get('version') or '')}"),
            ('基准目录', esc(info.get('base_dir') or '-')),
            ('账号目录', esc(info.get('accounts_dir') or '-')),
            ('数据目录', esc(info.get('data_dir') or '-')),
            ('插件目录', esc(info.get('plugin_dir') or '-')),
        ])}
        <table class="el-table" style="margin-top:10px"><tr><th width="150">项目</th><th>值</th></tr>
          <tr><td>连接方式</td><td id="transportMode">-</td></tr></table>
      </div></div>"""


# ---------------------------------------------------------------- 账号（多账号列表 + 新建）
def _account_card(a):
    aid = esc(a.get('account_id') or '')
    running = bool(a.get('running'))
    return f"""
      <div class="stat-card" style="align-items:flex-start;flex-direction:column;gap:10px">
        <div style="display:flex;align-items:center;gap:10px;width:100%">
          <div class="stat-icon blue" style="width:36px;height:36px">🤖</div>
          <div style="min-width:0;flex:1">
            <div style="font-size:14px;font-weight:600">{esc(a.get('display_name') or aid)}</div>
            <div style="font-size:11.5px;color:var(--text-secondary)">ID {aid} · {esc(a.get('backend_name') or '')}</div>
          </div>
          {status_tag(running)}
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;width:100%">
          <button class="btn btn-primary" data-act="start" data-key="{aid}" style="padding:6px 14px">▶ 启动</button>
          <button class="btn btn-default" data-act="stop" data-key="{aid}" style="padding:6px 14px">⏹ 停止</button>
          <button class="btn btn-default" data-act="edit-account" data-key="{aid}" style="padding:6px 14px">✏️ 编辑</button>
          <button class="btn btn-default" data-act="del-account" data-key="{aid}" style="padding:6px 14px">🗑 删除</button>
        </div>
      </div>"""


def _accounts(ctx):
    accounts = ctx.get('accounts') or []
    cur = esc(ctx.get('current') or '')
    editing = ctx.get('editing') or {}
    body = ('<div class="stat-grid">' + ''.join(_account_card(a) for a in accounts) + '</div>'
            if accounts else
            '<div style="padding:20px;color:var(--text-secondary);text-align:center">还没有账号，用下面的表单新建一个</div>')

    # 新建/编辑：同一个表单，编辑时填好现值
    is_edit = bool(editing)
    t = {
        'id': esc(editing.get('account_id') or ''),
        'name': esc(editing.get('display_name') or ''),
        'ws': esc(editing.get('ws_url') or 'ws://127.0.0.1:3001'),
        'http': esc(editing.get('http_url') or 'http://127.0.0.1:3000'),
        'token': esc(editing.get('access_token') or ''),
    }
    form = f"""
      <div class="cf-form">
        <div class="cf-group">{'编辑账号 ' + t['id'] if is_edit else '新建账号'}</div>
        <div class="cf-field"><label class="cf-label" for="ac_id">账号 ID（QQ 号）</label>
          <input type="text" id="ac_id" value="{t['id']}" {'readonly' if is_edit else ''}
                 placeholder="例如 10001" data-acct="account_id"></div>
        <div class="cf-field"><label class="cf-label" for="ac_name">显示名</label>
          <input type="text" id="ac_name" value="{t['name']}" placeholder="例如 依星" data-acct="display_name"></div>
        <div class="cf-field"><label class="cf-label" for="ac_ws">WebSocket 地址</label>
          <input type="text" id="ac_ws" value="{t['ws']}" data-acct="ws_url">
          <div class="cf-help">NapCat 的 OneBot WebSocket，例如 ws://127.0.0.1:3001</div></div>
        <div class="cf-field"><label class="cf-label" for="ac_http">HTTP 地址</label>
          <input type="text" id="ac_http" value="{t['http']}" data-acct="http_url">
          <div class="cf-help">用于发文件/取群列表等，例如 http://127.0.0.1:3000</div></div>
        <div class="cf-field"><label class="cf-label" for="ac_token">Access Token</label>
          <input type="password" id="ac_token" value="{t['token']}" autocomplete="new-password" data-acct="access_token">
          <div class="cf-help">NapCat 里配置的 token，没设就留空</div></div>
        <div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
          <button class="btn btn-primary" data-act="{'save-account' if is_edit else 'create-account'}"
                  data-key="{t['id']}">{'保存修改' if is_edit else '创建账号'}</button>
          {'<button class="btn btn-default" data-act="cancel-edit">取消</button>' if is_edit else ''}
        </div>
        <div id="acMsg" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
      </div>"""

    return (card('👥 账号列表', body,
                 extra=f'<span style="font-size:12px;color:var(--text-secondary)">{len(accounts)} 个账号</span>')
            + card('➕ 新建 / 编辑账号', form))


# ---------------------------------------------------------------- 运行方式 / 安全设置
def _settings_runtime(ctx):
    st = (ctx.get('runtime') or {}).get('state') or {}
    modes = (ctx.get('runtime') or {}).get('modes') or {}
    opts = ''.join(
        f'<option value="{esc(k)}"{" selected" if k == st.get("backend_mode") else ""}>'
        f'{esc(v.get("label") or k)}</option>' for k, v in modes.items())
    desc = ''.join(
        f'<div class="cf-help" style="margin:2px 0"><b>{esc(v.get("label") or k)}</b>：{esc(v.get("desc") or "")}'
        + (f'（风险：{esc(v.get("risk"))}）' if v.get('risk') else '') + '</div>' for k, v in modes.items())
    return card('🛠️ 运行方式', f"""
      <div class="cf-form">
        <div class="cf-field"><label class="cf-label" for="rt_mode">后端模式</label>
          <select id="rt_mode" data-rt="backend_mode">{opts or '<option value="">（当前没有可选项）</option>'}</select>
          <div class="cf-help">决定机器人怎么连接 QQ（内置 / 外部 NapCat 等）</div></div>
        <div style="margin:6px 0 12px">{desc}</div>
        {pp.info_table([
            ('向导是否完成', '是' if st.get('wizard_completed') else '否'),
            ('风险已确认', '是' if st.get('risk_acknowledged') else '否'),
            ('需要重跑向导', '是' if st.get('needs_wizard') else '否'),
        ])}
        <div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
          <button class="btn btn-default" data-act="reset-wizard">🔄 重跑启动向导</button>
        </div>
        <div id="rtMsg" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
      </div>""")


def _settings_auth(ctx):
    a = ctx.get('auth') or {}
    cfg = a.get('config') or {}
    mode = cfg.get('mode') or a.get('mode') or 'none'
    opts = ''.join(f'<option value="{m}"{" selected" if m == mode else ""}>{lbl}</option>'
                   for m, lbl in (('none', '不鉴权（仅本机）'), ('password', '密码'), ('token', '令牌')))
    tok = esc(cfg.get('token') or '')
    return card('🔒 安全设置', f"""
      <div class="cf-form">
        <div class="cf-field"><label class="cf-label" for="au_mode">鉴权方式</label>
          <select id="au_mode" data-auth="mode">{opts}</select>
          <div class="cf-help">对外开 Web 访问时建议至少用密码；进程内（桌面窗口）调用不受影响</div></div>
        <div class="cf-field"><label class="cf-label" for="au_pwd">设置/修改密码</label>
          <input type="password" id="au_pwd" autocomplete="new-password" placeholder="留空表示不修改" data-auth="password"></div>
        {('<div class="cf-field"><label class="cf-label">当前令牌</label>'
          '<input type="text" value="' + tok + '" readonly></div>') if tok else ''}
        <div style="display:flex;gap:10px;margin-top:12px;flex-wrap:wrap">
          <button class="btn btn-default" data-act="rotate-token">🎲 重新生成令牌</button>
        </div>
        <div id="auMsg" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
      </div>""")


PAGES = {
    'dashboard': (_dashboard, '概览', []),
    'accounts': (_accounts, '账号管理', []),
    'config': (pp.config_page, '插件配置', ['save', 'save_restart']),
    'logs': (lambda ctx: pp.logs_page(ctx, '运行日志', '运行日志（最近 200 条）'), '运行日志', []),
    'settings-runtime': (_settings_runtime, '运行方式', ['save']),
    'settings-auth': (_settings_auth, '安全设置', ['save']),
}

# 设置页保存按钮的标题
ACTION_TITLES = {
    'config': lambda ctx: f'正在配置：{ctx.get("plugin_name") or ctx.get("plugin_key") or ""}',
    'settings-runtime': lambda ctx: '运行方式',
    'settings-auth': lambda ctx: '安全设置',
}


def render_page(page, ctx):
    fn, title, actions = PAGES.get(page, PAGES['dashboard'])
    at = ACTION_TITLES.get(page)
    return {
        'page': page,
        'crumb': title,
        'html': fn(ctx),
        'actions': actions,
        'action_title': at(ctx) if at else (title if actions else ''),
        'generated_at': int(time.time()),
    }


def render_status(ctx):
    """状态区片段（通用形状：{html:{id:内容}, text:{id:文本}}）——与 Linux 端同一套"""
    acc = ctx.get('account') or {}
    aid = esc(acc.get('account_id') or '')
    running = bool(acc.get('running'))
    connected = bool(acc.get('connected'))
    return {
        'html': {
            'botStatusArea': (status_tag(running)
                              + f'<span style="font-size:13px;font-weight:600">{esc(acc.get("display_name") or aid)}</span>'
                              + f'<span style="font-size:12px;color:var(--text-secondary)">'
                              + f'{esc(acc.get("backend") or acc.get("backend_name") or "")}'
                              + (f' · {esc(acc.get("plugin_count"))} 个插件' if acc.get('plugin_count') is not None else '')
                              + '</span>'),
            'dashBotDesc': f'{esc(acc.get("display_name") or aid)} · {esc(acc.get("backend_name") or "")}',
        },
        'text': {
            'dashBotVal': '运行中' if running else '已停止',
            'dashBotDesc': f'{acc.get("display_name") or aid} · {acc.get("backend_name") or ""}',
        },
        'running': running,
        'connected': connected,
    }
