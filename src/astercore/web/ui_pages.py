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


def _account_row(a):
    """账号**横向长条**（用户要求：列表里账号为横向长条，操作按钮在同一行右侧）"""
    aid = esc(a.get('account_id'))
    running = bool(a.get('running'))
    backend = esc(a.get('backend_name') or '')
    name = esc(a.get('display_name') or a.get('account_id'))
    initial = (str(a.get('display_name') or aid or '?')[:1])
    tag = ('<span class="el-tag success"><span class="status-dot green"></span> 运行中</span>'
           if running else
           '<span class="el-tag muted"><span class="status-dot red"></span> 已停止</span>')
    return f"""
      <div class="acct-line" style="display:flex;align-items:center;gap:12px;padding:10px 12px;
           border:1px solid var(--border,#e5e7eb);border-radius:10px;margin:8px 0">
        <div style="width:34px;height:34px;border-radius:50%;background:#2b6cff;color:#fff;
             display:flex;align-items:center;justify-content:center;font-size:14px;flex:0 0 auto">{esc(initial)}</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600">{name}</div>
          <div style="font-size:12px;color:var(--text-secondary)">ID {aid} · {backend}</div>
        </div>
        <div style="flex:0 0 auto">{tag}</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;flex:0 0 auto">
          <button class="btn btn-primary mini" data-act="start" data-key="{aid}">启动</button>
          <button class="btn btn-default mini" data-act="stop" data-key="{aid}">停止</button>
          <button class="btn btn-default mini" data-act="acct-edit" data-key="{aid}"
            data-ws="{esc(a.get('ws_url') or '')}" data-http="{esc(a.get('http_url') or '')}"
            data-token="{esc(a.get('access_token') or '')}" data-name="{name}">编辑</button>
          <button class="btn btn-default mini" data-act="del-account" data-key="{aid}">删除</button>
        </div>
      </div>"""


def _acct_dialog() -> str:
    """添加/编辑账号的**弹窗**（替代原来页面底部的内嵌表单；字段与 data-acct 约定不变）"""
    return """
    <div class="ui-modal" id="acctDlg" role="dialog" aria-modal="true" hidden>
      <div class="ui-modal-backdrop" data-act="acct-dlg-cancel"></div>
      <div class="ui-modal-box">
        <h2 class="ui-modal-title" id="acctDlgTitle">添加账号</h2>
        <div class="cf-form">
          <div class="cf-field"><label class="cf-label" for="ac_id">账号 ID（QQ 号）</label>
            <input type="text" id="ac_id" placeholder="例如 10001" data-acct="account_id"></div>
          <div class="cf-field"><label class="cf-label" for="ac_name">显示名</label>
            <input type="text" id="ac_name" placeholder="例如 依星" data-acct="display_name"></div>
          <div class="cf-field"><label class="cf-label" for="ac_ws">WebSocket 地址</label>
            <input type="text" id="ac_ws" value="ws://127.0.0.1:3001" data-acct="ws_url">
            <div class="cf-help">NapCat 的 OneBot WebSocket，例如 ws://127.0.0.1:3001</div></div>
          <div class="cf-field"><label class="cf-label" for="ac_http">HTTP 地址</label>
            <input type="text" id="ac_http" value="http://127.0.0.1:3000" data-acct="http_url"></div>
          <div class="cf-field"><label class="cf-label" for="ac_token">Access Token</label>
            <input type="password" id="ac_token" autocomplete="new-password" data-acct="access_token">
            <div class="cf-help">NapCat 里配置的 token，没设就留空</div></div>
          <div id="acMsg" style="font-size:12px;color:var(--text-secondary);margin-top:8px"></div>
        </div>
        <div class="ui-modal-actions">
          <button class="btn btn-default" data-act="acct-dlg-cancel">取消</button>
          <button class="btn btn-primary" data-act="save-account" id="acctDlgSave">保存</button>
        </div>
      </div>
    </div>"""


def _accounts(ctx):
    """账号页（用户要求）：列表里**横向长条**；新建/编辑移到列表右上角**弹窗**"""
    accounts = ctx.get('accounts') or []
    body = (''.join(_account_row(a) for a in accounts) if accounts else
            '<div style="padding:20px;color:var(--text-secondary);text-align:center">'
            '还没有账号，点右上角「＋ 添加账号」新建一个</div>')
    extra = ('<span style="display:flex;gap:8px;align-items:center">'
             f'<span style="font-size:12px;color:var(--text-secondary)">{len(accounts)} 个账号</span>'
             '<button class="btn btn-primary mini" data-act="acct-add">＋ 添加账号</button></span>')
    return card('👥 账号列表', body, extra=extra) + _acct_dialog()




# ---------------------------------------------------------------- 运行方式 / 安全设置
def _settings_runtime(ctx):
    """运行方式：**单选**选择后端模式；右侧按大类分成多张卡片（用户要求）。"""
    st = (ctx.get('runtime') or {}).get('state') or {}
    modes = (ctx.get('runtime') or {}).get('modes') or {}
    cur = str(st.get('backend_mode') or '')
    picks = ''.join(
        f'<label class="cf-field" style="display:flex;gap:10px;align-items:flex-start;'
        f'border:1px solid var(--border,#e5e7eb);border-radius:10px;padding:10px 12px;margin:8px 0;cursor:pointer">'
        f'<input type="radio" name="rt_pick" value="{esc(k)}" data-act="rt-pick"'
        f'{" checked" if k == cur else ""} style="margin-top:3px">'
        f'<span><b>{esc(v.get("label") or k)}</b>'
        + (f' <span class="el-tag info">风险 {esc(v.get("risk"))}/5</span>' if v.get('risk') else '')
        + f'<div class="cf-help" style="margin-top:2px">{esc(v.get("desc") or "")}</div></span></label>'
        for k, v in modes.items())
    return (
        card('🛠️ 后端模式（单选）', f"""
          <div class="cf-form">
            <div class="cf-help" style="margin-bottom:6px">
              决定机器人怎么连接 QQ。选中的模式会写进
              <code>runtime.json</code>，下次启动按它直启。
            </div>
            {picks or '<div class="cf-help">（当前没有可选项）</div>'}
            <input type="hidden" id="rt_mode" data-rt="backend_mode" value="{esc(cur)}">
            <div id="rtMsg" style="font-size:12px;color:var(--text-secondary);margin-top:6px"></div>
          </div>""")
        + card('📋 当前状态', pp.info_table([
            ('向导是否完成', '是' if st.get('wizard_completed') or st.get('inited') else '否'),
            ('风险已确认', '是' if st.get('risk_acknowledged') else '否'),
            ('需要重跑向导', '是' if st.get('needs_wizard') else '否'),
        ]))
        + card('⚙️ 操作', """
          <div style="display:flex;gap:10px;flex-wrap:wrap">
            <button class="btn btn-default" data-act="reset-wizard">🔄 重跑启动向导</button>
          </div>
          <div class="cf-help" style="margin-top:8px">
            「重跑启动向导」会把引导状态清空，下次启动重新选使用方式（不影响已建账号）。
          </div>""")
    )


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



def _settings_page(ctx):
    """页面与端口（规格 §三 卡片二）。保存走已有的 /api/setup/page（校验/落盘都在那）。"""
    pg = ctx.get('page') or {}
    mode = pg.get('mode') or 'window'
    port = pg.get('port') or 34567
    return card('🖥️ 页面模式与端口', f"""
      <div class="cf-form">
        <div class="cf-field"><label class="cf-label">页面模式</label>
          <select id="sp_mode">
            <option value="window"{" selected" if mode == "window" else ""}>窗口模式（内嵌窗口）</option>
            <option value="port"{" selected" if mode == "port" else ""}>端口模式（浏览器访问）</option>
          </select>
          <div class="cf-help">窗口模式不需要任何端口；端口模式会在本机开一个 Web 端口供浏览器访问</div></div>
        <div class="cf-field"><label class="cf-label" for="sp_port">端口</label>
          <input type="number" id="sp_port" min="1" max="65535" value="{port}">
          <div class="cf-help">范围 1-65535，默认 34567（避开常用端口）。改动重启程序后生效</div></div>
        <div id="spMsg" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
      </div>""")

PAGES = {
    'dashboard': (_dashboard, '概览', []),
    'accounts': (_accounts, '账号管理', []),
    'config': (pp.config_page, '插件配置', ['save', 'save_restart']),
    'logs': (lambda ctx: pp.logs_page(ctx, '运行日志', '运行日志（最近 200 条）'), '运行日志', []),
    'settings-runtime': (_settings_runtime, '运行方式', ['save']),
    'settings-auth': (_settings_auth, '安全设置', ['save']),
    'settings-page': (_settings_page, '页面与端口', ['save']),
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


# ---------------------------------------------------------------- 连接引导（面板内一页）
# 用户要求：引导**不在首次启动拦人**，而是在"新建账号"时走这一步。
# 页面内容由后端生成，交互走 data-act（面板的事件委托），事件处理器在 index.html 的
# PANEL.actions 里，最终打到 /api/guide/*（桌面 file:// 模式下由 Transport 转 RPC）。
def _guide_page(ctx):
    """连接引导（面板内一页）

    用户要求：**页面主视线只展示 NapCat 三步安装引导**；其余（停止组件/看日志/刷新状态、
    一键建账号、内置协议说明）收进「更多操作」，需要时再展开。
    """
    comp_dir = esc((ctx.get('guide') or {}).get('base_dir') or '')
    return card('🚀 连接引导', f"""
      <div class="cf-help" style="margin-bottom:10px">
        推荐路线：<b>① 去官网下载并安装 NapCat</b>（走官方 QQ 通道，风控最低）
        → <b>② 写配置</b> → <b>③ 启动扫码</b>，然后把连接信息填到「账号」页（ws 地址 + 令牌）。
        <div style="margin-top:4px">第①步只做引导（不在应用里下载）：点击会用**浏览器**打开官方下载页
        <a href="https://github.com/NapNeko/NapCatQQ/releases/latest" target="_blank" rel="noreferrer">https://github.com/NapNeko/NapCatQQ/releases/latest</a>，
        装完回来点第②步即可。</div>
      </div>
      <div class="cf-form">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <button class="btn" data-act="gd-open" data-url="https://github.com/NapNeko/NapCatQQ/releases/latest">① 去官网下载 NapCat</button>
          <button class="btn" data-act="gd-config" data-key="napcat">② 写 NapCat 配置（自动端口/令牌）</button>
          <button class="btn" data-act="gd-host" data-key="napcat">③ 启动 NapCat 并取二维码</button>
        </div>
        <div id="gdOut" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
        <div id="gdCfg" style="margin-top:10px;display:none"></div>
        <div id="gdQR" style="margin-top:10px"></div>

        <details style="margin-top:14px">
          <summary style="cursor:pointer;font-size:13px;color:var(--text-secondary)">更多操作</summary>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
            <button class="btn btn-default" data-act="gd-stop" data-key="napcat">停止组件</button>
            <button class="btn btn-default" data-act="gd-log" data-key="napcat">看日志</button>
            <button class="btn btn-default" data-act="gd-refresh" data-key="napcat">刷新状态</button>
          </div>
          <pre id="gdLogBox" style="max-height:220px;overflow:auto;font-size:12px;
               background:var(--bg-secondary,#f6f7f9);padding:10px;border-radius:8px;display:none"></pre>
          <div class="cf-field" style="margin-top:12px"><label class="cf-label">用这串连接信息建账号（一键）</label>
            <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <input id="gdQQ" type="text" inputmode="numeric" placeholder="QQ 号，如 123456789" style="width:200px">
              <button class="btn" data-act="gd-create" data-key="napcat">一键建好并启动</button>
            </div>
            <div class="cf-help">会自动写入组件配置（端口/令牌与第②步一致）→ 建好账号 → 直接启动。</div></div>
          <div class="cf-help" style="margin-top:12px">组件目录：{comp_dir}</div>
          <div class="cf-help" style="margin-top:6px">
            <b>内置协议（Lagrange）由软件自带</b>，不在这里下载：装好本软件即可直接用
            （面板 → 设置 → 运行方式 → 内部协议）。
          </div>
        </details>
      </div>""")


_orig_accounts = _accounts          # 必须指向最新实现（否则改版不生效）


def _accounts_with_guide(ctx):
    banner = ('<div class="card"><div class="card-body" style="display:flex;gap:12px;align-items:center;'
              'flex-wrap:wrap"><div style="font-size:20px">🚀</div>'
              '<div style="flex:1;min-width:220px">第一次用？先走一遍<b>连接引导</b>：'
              '下载组件 → 写配置 → 启动扫码，再把连接信息填到下面的表单。</div>'
              '<button class="btn" data-open="guide-page">打开连接引导</button></div></div>')
    return banner + _orig_accounts(ctx)


PAGES['accounts'] = (_accounts_with_guide, PAGES['accounts'][1], PAGES['accounts'][2])
PAGES['guide-page'] = (_guide_page, '连接引导', [])


# ---------------------------------------------------------------- 关于与更新（计划书 §369）
def _about_page(ctx):
    from astercore import __version__
    upd = ctx.get('update') or {}
    state = upd.get('state') or 'idle'
    ver = upd.get('version') or ''
    msg = upd.get('message') or '点「检查更新」看看有没有新版本'
    return card('🔄 关于与更新', f"""
      <div class="cf-form">
        <div class="cf-field"><label class="cf-label">当前版本</label>
          <div><b>栖星 AsterCore v{__version__}</b>
            <span class="el-tag {'success' if state == 'done' else 'info'}">{esc(msg)}</span>
          </div>
          <div class="cf-help">更新流程：检查 → 下载 → <b>sha256 校验</b> → 暂存 → 重启替换；
            新版本起不来会自动回滚到旧版本。</div>
          <div class="cf-help">遇到问题先看程序目录里的 <b>docs/常见问题FAQ.md</b>（SmartScreen 解除锁定、
            引导流程、日志位置、数据搬迁、反馈模板）与 <b>使用说明.txt</b>。</div></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <button class="btn" data-act="up-check">检查更新</button>
          <button class="btn" data-act="up-start">下载并校验</button>
          <button class="btn" data-act="up-apply" {'disabled' if state != 'done' else ''}>重启以应用更新</button>
          <button class="btn btn-default" data-act="up-refresh">刷新状态</button>
        </div>
        <div id="upBox" style="font-size:12px;color:var(--text-secondary);margin-top:10px">
          版本 {esc(ver or '—')} ｜ 状态 {esc(state)}
        </div>
        <div id="upBar" style="height:6px;background:var(--bg-secondary,#eee);border-radius:3px;margin-top:8px;overflow:hidden">
          <div id="upBarFill" style="height:100%;width:{int(upd.get('percent') or 0)}%;background:#2b6cff"></div>
        </div>
      </div>""")


PAGES['about-page'] = (_about_page, '关于与更新', [])


def _save_row(kind: str, hint: str = "") -> str:
    """每块设置自己的保存按钮（三块合到一页后，底部单一「保存」没法区分目标）"""
    return f"""
      <div style="display:flex;gap:10px;align-items:center;margin:10px 0 4px">
        <button class="btn btn-primary" data-act="save-{kind}">💾 保存「{hint or kind}」</button>
        <span id="sv{kind}Msg" style="font-size:12px;color:var(--text-secondary)"></span>
      </div>"""


def _settings_all(ctx):
    """设置（合并页）：左侧只留一个「设置」入口，右侧三张卡片各管一块。

    用户要求：把「运行方式 / 安全设置 / 页面与端口」三个分类**做到设置页右侧**，
    左侧栏不再显示这三个选项。
    """
    return (
        '<div class="cf-help" style="margin-bottom:10px">'
        '这一页集中三块设置：<b>① 后端模式（单选）</b>、<b>② 安全设置</b>、<b>③ 页面与端口</b>。'
        '每块下方各有自己的「保存」按钮，改哪块存哪块。</div>'
        + _settings_runtime(ctx) + _save_row('rt', '后端模式')
        + _settings_auth(ctx) + _save_row('auth', '安全设置')
        + _settings_page(ctx) + _save_row('page', '页面与端口')
    )


PAGES['settings'] = (_settings_all, '设置', [])
