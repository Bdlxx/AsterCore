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

# 页面模式端口默认值：与 setup.py DEFAULT_PORT 同值（留空时后端就用这个）。
# 放在这里是为了让"面板占位文案"和"保存时的兜底"用同一个数。
DEFAULT_PAGE_PORT = 34567


# ---------------------------------------------------------------- 概览
def _conn_state(acc: dict) -> str:
    """后端连接文案（概览卡片与 /ui/status 轮询共用，别各写一份）"""
    if acc.get("connected"):
        return "已连接"
    return "未连接" if acc.get("running") else "未启动"


def _dashboard(ctx):
    acc = ctx.get('account') or {}
    info = ctx.get('info') or {}
    plugins = ctx.get('plugins') or []
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

    # 用户要求：账号状态/版本 在顶部栏已经有了，这两张卡去掉，别重复；
    # 只留"后端连接"（顶栏没有）与"已加载插件"。
    return f"""
      <div class="stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(240px,1fr))">
        <div class="stat-card"><div class="stat-icon green">🔗</div><div class="stat-info">
          <div class="label">后端连接</div>
          <div class="value" id="dashConnVal">{esc(_conn_state(acc))}</div>
          <div class="desc" id="dashConnDesc">{esc(acc.get('backend') or acc.get('backend_name') or '')}</div>
        </div></div>
        <div class="stat-card"><div class="stat-icon orange">🧩</div><div class="stat-info">
          <div class="label">已加载插件</div><div class="value">{len(plugins)} 个</div>
          <div class="desc">点击下方插件可直接配置</div>
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
            data-token="{esc(a.get('access_token') or '')}" data-name="{name}"
            data-backend="{esc(a.get('backend_name') or 'onebot')}">编辑</button>
          <button class="btn btn-default mini" data-act="del-account" data-key="{aid}">删除</button>
        </div>
      </div>"""


def _acct_dialog() -> str:
    """添加/编辑账号的**弹窗**（替代原来页面底部的内嵌表单；字段与 data-acct 约定不变）

    用户要求：登录模式（NapCat / 内置协议 / 演示）**在这里选**，设置页不再保留。
    并且**先选连接方式**（NapCat / 内置协议），选完才显示对应的填写项 ——
    两种方式的必填项完全不同，一股脑列出来只会让人填错。

    连接信息**不给用户填**：NapCat 那边由软件写配置文件（端口自动、令牌随机生成），
    账号跟着这份配置走，两边天然一致。只有自建了外部 NapCat 的人才需要手动，
    所以那三个框收进「手动填写」里。
    """
    return """
    <div class="ui-modal" id="acctDlg" role="dialog" aria-modal="true" hidden>
      <div class="ui-modal-backdrop" data-act="acct-dlg-cancel"></div>
      <div class="ui-modal-box">
        <h2 class="ui-modal-title" id="acctDlgTitle">添加账号</h2>
        <input type="hidden" id="ac_mode" data-acct="backend_name" value="onebot">
        <div id="acctStep1">
          <div class="cf-help" style="margin-bottom:8px">先选这个账号怎么连 QQ：</div>
          <button type="button" class="acct-pick" onclick="PANEL.hooks.acctPick('onebot')">
            <img class="acct-pick-ico" src="napcat.png" alt="">
            <span class="acct-pick-body"><b>NapCat</b><span class="cf-help">走官方 QQ
              客户端通道，风控最低。需要先下载并启动 NapCat（侧栏「NapCat」）。</span></span>
          </button>
          <button type="button" class="acct-pick" onclick="PANEL.hooks.acctPick('lagrange')">
            <span class="acct-pick-ico">🧩</span>
            <span class="acct-pick-body"><b>内置协议（实验）</b><span class="cf-help">软件自带，
              免安装、不用填连接信息；非官方协议，建议用小号。</span></span>
          </button>
        </div>
        <div class="cf-form" id="acctFields" style="display:none">
          <div class="cf-field"><label class="cf-label" for="ac_id">账号 ID（QQ 号）</label>
            <input type="text" id="ac_id" placeholder="例如 10001" data-acct="account_id"></div>
          <div class="cf-field"><label class="cf-label" for="ac_name">显示名</label>
            <input type="text" id="ac_name" placeholder="例如 依星" data-acct="display_name"></div>
          <div id="acctModeHelp" class="cf-help" style="margin:4px 0 2px"></div>
          <div id="acctConnFields">
            <div id="acctNcHint" class="cf-help" style="margin:2px 0 0"></div>
            <details id="acctManual" style="margin-top:10px">
              <summary style="cursor:pointer;font-size:12px;color:var(--text-secondary)">
                手动填写连接信息（只有自建的外部 NapCat 才需要）</summary>
              <div class="cf-field"><label class="cf-label" for="ac_ws">WebSocket 地址</label>
                <input type="text" id="ac_ws" placeholder="ws://127.0.0.1:3001" data-acct="ws_url">
                <div class="cf-help">留空即由软件自动生成端口并写进 NapCat 配置</div></div>
              <div class="cf-field"><label class="cf-label" for="ac_http">HTTP 地址</label>
                <input type="text" id="ac_http" placeholder="http://127.0.0.1:3000" data-acct="http_url"></div>
              <div class="cf-field"><label class="cf-label" for="ac_token">Access Token</label>
                <input type="password" id="ac_token" autocomplete="new-password" data-acct="access_token">
                <div class="cf-help">留空即随机生成一个令牌（推荐）</div></div>
            </details>
          </div>
          <div id="acMsg" style="font-size:12px;color:var(--text-secondary);margin-top:8px"></div>
        </div>
        <div class="ui-modal-actions">
          <button class="btn btn-default" data-act="acct-dlg-cancel">取消</button>
          <button class="btn btn-primary" data-act="save-account" id="acctDlgSave">保存</button>
        </div>
      </div>
    </div>
    <style>
      /* 连接方式选择卡（桌面专属页面标记，不动双端共用的 panel.css） */
      #acctDlg .acct-pick{display:flex;gap:10px;align-items:flex-start;width:100%;text-align:start;
        border:1px solid var(--border-color);border-radius:10px;background:transparent;padding:10px 12px;
        margin:8px 0;cursor:pointer;font-family:inherit;font-size:13px;color:var(--text-regular);
        transition:border-color .16s var(--ease,ease),background-color .16s var(--ease,ease)}
      #acctDlg .acct-pick:hover{border-color:var(--color-primary);background:var(--bg-hover);
        color:var(--text-primary)}
      #acctDlg .acct-pick-ico{width:32px;height:32px;flex:0 0 auto;display:flex;align-items:center;
        justify-content:center;font-size:24px}
      #acctDlg .acct-pick-body{display:flex;flex-direction:column;gap:2px;min-width:0}
    </style>"""


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
        + _runtime_status_cards(ctx)
    )


def _runtime_status_cards(ctx) -> str:
    """当前状态 + 操作（原「运行方式」页的后两张卡）。

    用户要求：登录模式改到**账号弹窗**里选，设置页不再保留后端模式单选 ——
    所以合并页只留这两张状态卡，模式单选仍留在独立的 /settings-runtime 页。
    """
    st = (ctx.get('runtime') or {}).get('state') or {}
    return (card('📋 当前状态', pp.info_table([
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
          </div>"""))


def _settings_auth(ctx):
    """安全设置（用户要求：移除令牌鉴权，只留 不鉴权 / 密码）。"""
    a = ctx.get('auth') or {}
    cfg = a.get('config') or {}
    mode = cfg.get('mode') or a.get('mode') or 'none'
    choices = [('none', '不鉴权（仅本机）'), ('password', '密码')]
    legacy = ''
    if mode == 'token':
        # 老配置可能还停在"令牌"上：保留一个**禁用**项把真实状态显示出来，
        # 否则下拉框会显示"不鉴权"而实际是令牌（那等于悄悄把鉴权关掉）。
        choices.append(('token', '令牌（已下线）'))
        legacy = ('<div class="cf-help" style="color:var(--color-danger)">'
                  '当前用的是「令牌鉴权」，该方式已下线 —— 请选「密码」并设置密码后保存。</div>')
    opts = ''.join(
        f'<option value="{m}"{" selected" if m == mode else ""}'
        f'{" disabled" if m == "token" else ""}>{lbl}</option>' for m, lbl in choices)
    return card('🔒 安全设置', f"""
      <div class="cf-form">
        <div class="cf-field"><label class="cf-label" for="au_mode">鉴权方式</label>
          <select id="au_mode" data-auth="mode">{opts}</select>
          <div class="cf-help">对外开 Web 访问时建议至少用密码；进程内（桌面窗口）调用不受影响</div>{legacy}</div>
        <div class="cf-field"><label class="cf-label" for="au_pwd">设置/修改密码</label>
          <input type="password" id="au_pwd" autocomplete="new-password" placeholder="留空表示不修改" data-auth="password"></div>
        <div id="auMsg" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
      </div>""")



def _settings_page(ctx):
    """页面与端口（规格 §三 卡片二）。保存走已有的 /api/setup/page（校验/落盘都在那）。

    端口框**用户没设过就留空**，默认值只做灰色占位：以前把内部默认值当"已填值"
    显示出来，用户分不清那是自己设的还是默认的（用户要求）。
    """
    pg = ctx.get('page') or {}
    mode = pg.get('mode') or 'window'
    port = pg.get('port') or ''                     # 空 = 没显式设过
    dflt = pg.get('default_port') or DEFAULT_PAGE_PORT
    return card('🖥️ 页面模式与端口', f"""
      <div class="cf-form">
        <div class="cf-field"><label class="cf-label">页面模式</label>
          <select id="sp_mode">
            <option value="window"{" selected" if mode == "window" else ""}>窗口模式（内嵌窗口）</option>
            <option value="port"{" selected" if mode == "port" else ""}>端口模式（浏览器访问）</option>
          </select>
          <div class="cf-help">窗口模式不需要任何端口；端口模式会在本机开一个 Web 端口供浏览器访问</div></div>
        <div class="cf-field"><label class="cf-label" for="sp_port">端口</label>
          <input type="number" id="sp_port" min="1" max="65535" value="{esc(port)}"
                 placeholder="{esc(dflt)}" autocomplete="off">
          <div class="cf-help">范围 1-65535，默认 {esc(dflt)}。留空即用默认，改动重启程序后生效</div></div>
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
    running = bool(acc.get('running'))
    connected = bool(acc.get('connected'))
    return {
        # 只喂**页面上真的存在**的锚点：账号状态/版本卡已按用户要求移除，
        # 顶栏那份由 headerStatus 轮询负责，这里只管概览的「后端连接」卡。
        'text': {
            'dashConnVal': _conn_state(acc),
            'dashConnDesc': acc.get('backend') or acc.get('backend_name') or '',
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
    # 组件目录**必须问 components 模块**（它按运行基目录解析），不能自己拼字符串 ——
    # 以前兜底里写死 `\components\napcat`，换个安装目录/换个组件就对不上了（用户要求）。
    try:
        from astercore import components as _C
        from astercore.paths import app_base_dir as _abd
        comp_dir = esc(str(_C.component_dir(_abd(), 'napcat')))
    except Exception:                                  # noqa: BLE001
        comp_dir = '（组件目录未确定，先看「更多操作 → 看日志」里的路径）'
    return card('🚀 连接引导', f"""
      <div class="cf-help" style="margin-bottom:10px">
        三步走：<b>① 一键下载并解压 NapCat</b>（官方 Windows 版，走官方 QQ 通道，风控最低）
        → <b>② 写配置</b> → <b>③ 启动扫码</b>。扫码登录后，本页会问你要不要直接用
        刚登录的账号建号，点一下就能用。
        <div style="margin-top:4px">下载进度看顶部栏（下载中 x% / 解压中），
        官方压缩包会自动解压到组件目录：<code id="gdDir" style="user-select:all">{comp_dir}</code></div>
      </div>
      <div class="cf-form">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <button class="btn btn-primary" data-act="gd-install" data-key="napcat">① 下载并解压 NapCat</button>
          <button class="btn" data-act="gd-config" data-key="napcat">② 写 NapCat 配置（自动端口/令牌）</button>
          <button class="btn" data-act="gd-host" data-key="napcat">③ 启动 NapCat 并取二维码</button>
        </div>
        <div id="gdJob" class="cf-help" style="margin-top:8px"></div>
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
            （在「账号」页点「添加账号」，第一步选「内置协议」即可）。
          </div>
        </details>
      </div>""")


# ---------------------------------------------------------------- NapCat 管理（侧栏常驻入口）
def _napcat_state_html(comp: dict) -> str:
    """NapCat 状态片段（页面首屏由后端渲染，之后每次刷新都用它替换）"""
    comp = comp or {}
    installed = bool(comp.get("installed"))
    running = bool(comp.get("running"))
    install_tag = ('<span class="el-tag success"><span class="status-dot green"></span> 已安装</span>'
                   if installed else
                   '<span class="el-tag warning"><span class="status-dot yellow"></span> 未安装</span>')
    run_tag = ('<span class="el-tag success"><span class="status-dot green"></span> 运行中</span>'
               if running else
               '<span class="el-tag muted"><span class="status-dot red"></span> 未运行</span>')
    exe = esc(comp.get("executable") or "—")
    pid = comp.get("pid")
    return (f'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">'
            f'{install_tag}{run_tag}'
            + (f'<span class="el-tag muted">PID {esc(pid)}</span>' if pid else '')
            + '</div>'
            f'<table class="el-table" style="margin-top:10px">'
            f'<tr><th width="150">项目</th><th>值</th></tr>'
            f'<tr><td>可执行文件</td><td style="word-break:break-all">{exe}</td></tr>'
            f'<tr><td>组件目录</td><td style="word-break:break-all">{esc(comp.get("dir") or "—")}</td></tr>'
            f'</table>')


def _napcat_page(ctx):
    """NapCat 管理页（侧栏「概览」下面的常驻图标进来）：

    装/起/停/扫码/日志都在这，等价于把「连接引导」里 NapCat 那三步单独拎出来，
    因为它是最常用的日常操作（重装、重启、重新扫码）。
    """
    comp = ((ctx.get("components") or {}).get("napcat") or {})
    installed = bool(comp.get("installed"))
    return (card('🐱 NapCat 管理', f"""
      <div class="cf-help" style="margin-bottom:8px">
        官方 Windows 版（NapCat.Shell）下载后自动解压到组件目录，不需要手动解压。
        下载进度显示在顶部栏；装好后再写配置、启动扫码。
      </div>
      <div id="ncStatus">{_napcat_state_html(comp)}</div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
        <button class="btn {'btn-primary' if not installed else 'btn-default'}"
                data-act="gd-install" data-key="napcat">下载并解压</button>
        <button class="btn btn-primary" data-act="gd-host" data-key="napcat">启动并取二维码</button>
        <button class="btn btn-default" data-act="gd-stop" data-key="napcat">停止</button>
        <button class="btn btn-default" data-act="gd-config" data-key="napcat">写配置（自动端口/令牌）</button>
        <button class="btn btn-default" data-act="gd-log" data-key="napcat">看日志</button>
        <button class="btn btn-default" data-act="gd-refresh" data-key="napcat">刷新状态</button>
      </div>
      <div id="gdJob" class="cf-help" style="margin-top:8px"></div>
      <div id="gdOut" style="font-size:12px;color:var(--text-secondary);margin-top:10px"></div>
      <div id="gdCfg" style="margin-top:10px;display:none"></div>
      <div id="gdQR" style="margin-top:10px"></div>
      <pre id="gdLogBox" style="max-height:260px;overflow:auto;font-size:12px;display:none;
           background:var(--bg-secondary,#f6f7f9);padding:10px;border-radius:8px"></pre>"""))


_orig_accounts = _accounts          # 必须指向最新实现（否则改版不生效）


def _accounts_with_guide(ctx):
    banner = ('<div class="card"><div class="card-body" style="display:flex;gap:12px;align-items:center;'
              'flex-wrap:wrap"><div style="font-size:20px">🚀</div>'
              '<div style="flex:1;min-width:220px">第一次用？先走一遍<b>连接引导</b>：'
          '下载组件 → 写配置 → 启动扫码，再用拿到的连接信息添加账号。</div>'
              '<button class="btn" data-open="guide-page">打开连接引导</button></div></div>')
    return banner + _orig_accounts(ctx)


PAGES['accounts'] = (_accounts_with_guide, PAGES['accounts'][1], PAGES['accounts'][2])
PAGES['guide-page'] = (_guide_page, '连接引导', [])
PAGES['napcat'] = (_napcat_page, 'NapCat 管理', [])


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


def _auto_hint(kind: str) -> str:
    """每块设置只留一行状态字：改完立即保存，没有「保存」按钮。

    用户要求："本页不保留保存按钮，就像正常软件的设置那样" —— 所以这里不再出按钮，
    只保留回显（"已保存 / 保存失败"）用的元素，保存动作由前端 change 事件触发。
    """
    return f"""
      <div style="display:flex;gap:10px;align-items:center;margin:10px 0 4px">
        <span id="sv{kind}Msg" style="font-size:12px;color:var(--text-secondary)"></span>
      </div>"""


def _settings_all(ctx):
    """设置（合并页）：左侧只留一个「设置」入口，右侧三张卡片各管一块。

    用户要求：把设置分类**做到设置页右侧**，左侧栏不再显示这些选项；
    并且「登录模式」不在这页（移到账号弹窗里选）。
    """
    return (
        '<div class="cf-help" style="margin-bottom:10px">'
        '这一页集中三块设置：<b>① 当前状态</b>、<b>② 安全设置</b>、<b>③ 页面与端口</b>。'
        '改完立即生效并保存，不需要点保存按钮。'
        '<br>登录模式（NapCat / 内置协议 / 演示）在「账号」页的添加、编辑弹窗里选。</div>'
        + _runtime_status_cards(ctx)
        + _settings_auth(ctx) + _auto_hint('auth')
        + _settings_page(ctx) + _auto_hint('page')
    )


PAGES['settings'] = (_settings_all, '设置', [])
