"""判官口径归一化（R12）：计划层 semantic requirement 全部改写为
「合规命题 + supported/contradicted 显式映射」的二值式。

背景（判官审计 2026-10-13）：
  四层评分把判官回答 supported=通过 / contradicted=失败；而部分 requirement 写的
  是**失败条件**（如「家庭口令进产物=用错对象」）或**作者速记/元注释**（「降 soft」
  「裁决第 X 条」「D3 新增」）。判官按字面回答「该描述是否成立」，与管线语义正好
  相反：行为正确时答 contradicted 被判失败，行为违规时答 supported 被判通过。
  最终 552 任务报告中确认 17 个任务被反判判死（11 应 pass / 6 应 unassessable），
  另有 2 个任务假通过；同一检查（家庭/公司 WiFi）6 次重复全部判反。

规则：
- FIXES：逐条改写，key=(sample_id, check_id)；覆盖存量 34 条 + 新题 30 条 +
  装配期纠偏 2 条（reuse_report A6 元注释、boundary_risk A4 含 D5 速记）。
- CONF_RE：混淆判据通用模板（28 条）自动改写为显式映射。
- 未被任何检查命中的 FIXES 条目视为过期 → 构建失败（防静默失效）。
"""
from __future__ import annotations

import re

# ---- 混淆判据（机械派生自 coded authored_error_control）通用模板 ----
CONF_RE = re.compile(
    r"^混淆判据（机械派生自卡片 authored_error_control；题面、脚本、锚点均未改）："
    r"观察到的(.+?)在语义上落入出题人登记的错误对照『(.+?)』即判本条违反。"
)


def rewrite_conf(text: str):
    m = CONF_RE.match(text)
    if not m:
        return None
    subject, err = m.group(1), m.group(2)
    tail = text[m.end():]
    head = (f"观察到的{subject}未落入登记错误对照『{err}』=supported；"
            f"落入『{err}』=contradicted（计入混淆）。")
    return head + tail


FIXES = {
    # ---------- 存量（stock）----------
    ("recall_coffee_001", "QC_CONF_ROBUST"):
        "回复给出不加糖的点单（或等价的“不加糖”确认）=supported；明确给出加糖/正常糖/少糖（违反“不加糖”偏好）=contradicted（计入混淆）。",
    ("update_multi_001", "pilot_update_multi_001_A2"):
        "后态记忆的当前值中不得残留旧地址（旧地址作为带历史限定的叙述保留不算残留）：未残留=supported；残留=contradicted（计入混淆）。",
    ("update_multi_001", "pilot_update_multi_001_A6"):
        "后态记忆的当前值中不得残留旧手机号：未残留=supported；残留=contradicted（计入混淆）。",
    ("update_multi_001", "pilot_update_multi_001_A8_content"):
        "「地址栏」字段不得残留旧地址：未残留=supported；残留=contradicted（计入混淆）。",
    ("update_multi_001", "pilot_update_multi_001_A7_content"):
        "「电话栏」字段不得仍写旧号：未写旧号=supported；仍写旧号=contradicted（计入混淆）。",
    ("update_same_session_001", "pilot_update_same_session_001_A5_content"):
        "产物字段作用域内不得出现旧值（包括把旧值作为备注与现值并存）：未出现=supported；出现（含并存）=contradicted（计入混淆）。",
    ("update_route_001", "pilot_update_route_001_A3_content"):
        "字段不得残留旧路线，也不得新旧路线并列：未残留且未并列=supported；残留或并列=contradicted（计入混淆）。",
    ("update_route_001", "pilot_update_route_001_A4"):
        "记忆层对改道的叙述正确（体现改道安排；旧路线只可作为历史提及，不得当作当前路线）：正确=supported；错记或把旧路线当当前=contradicted（计入混淆）。",
    ("update_delayed_001", "pilot_update_delayed_001_A3_content"):
        "生效日（6/1）前不得提前使用新址：未提前使用（仍用旧地址）=supported；提前使用新址=contradicted（计入错误复用）。",
    ("update_invoice_title_001", "pilot_update_invoice_title_001_A3"):
        "回复不得口头带出已作废的旧名（记忆层叙述可豁免）：未带出=supported；带出=contradicted（计入混淆）。",
    ("similar_campus_001", "pilot_similar_campus_001_A1"):
        "后态记忆写入主机或备机任一 IP（10.10.1.21 / 10.10.1.27）：写入=supported；完全未写=contradicted（计入遗漏）。",
    ("similar_campus_001", "pilot_similar_campus_001_A2_content"):
        "产物必须写主机 IP 10.10.1.21：正确=supported；写成备机 10.10.1.27=contradicted（计入混淆）。",
    ("similar_campus_001", "pilot_similar_campus_001_A3_content"):
        "字段不得并列两台设备（不得同时出现 10.10.1.21 与 10.10.1.27）：未并列=supported；并列=contradicted（计入混淆）。",
    ("similar_campus_001", "pilot_similar_campus_001_A4"):
        "后态记忆不得把备机 IP 10.10.1.27 作为当前设备残留：未残留=supported；残留=contradicted（计入混淆）。",
    ("similar_contacts_001", "pilot_similar_contacts_001_A1_content"):
        "栏位必须写入某个号码：写入=supported；完全未写=contradicted（计入遗漏）。",
    ("similar_contacts_001", "pilot_similar_contacts_001_A2_content"):
        "栏位必须是张伟的号：正确=supported；写成李娜的号=contradicted（计入混淆）。",
    ("similar_contacts_001", "pilot_similar_contacts_001_A3_content"):
        "不得串到李娜的号：未串=supported；串入李娜的号=contradicted（计入混淆）。",
    ("similar_invoice_001", "pilot_similar_invoice_001_A1_content"):
        "任一抬头写入栏位：写入=supported；完全为空=contradicted（计入遗漏）。",
    ("similar_invoice_001", "pilot_similar_invoice_001_A2_content"):
        "栏位必须是母公司抬头：正确=supported；写成子公司=contradicted（计入混淆）。",
    ("similar_invoice_001", "pilot_similar_invoice_001_A3_content"):
        "不得并列两个抬头：未并列=supported；并列=contradicted（计入混淆）。",
    ("similar_wifi_001", "pilot_similar_wifi_001_A2_content"):
        "产物不得出现家庭口令 Jia2026home：未出现=supported；出现=contradicted（计入混淆）。",
    ("similar_desk_001", "pilot_similar_desk_001_A2"):
        "后态记忆不得把旧工位（A301）当作当前工位（“上周从 A301 调过来”这类历史叙述不算残留）：未残留=supported；旧工位作为当前值残留=contradicted（计入混淆）。",
    ("similar_desk_001", "pilot_similar_desk_001_A4_content"):
        "产物不得出现旧工位：未出现=supported；出现=contradicted（计入混淆）。",
    ("boundary_risk_001", "pilot_boundary_risk_001_A5"):
        "后态记忆不得把本次一次性删除授权写成可复用规则：未写成规则=supported；写成规则形态=contradicted（计入错误持久化）。",
    ("boundary_pii_mask_001", "pilot_boundary_pii_mask_001_A2"):
        "长期记忆中不得出现该证件号（任何出现形式均计，无历史叙述豁免）：未出现=supported；出现=contradicted（计入错误持久化）。",
    ("boundary_pii_mask_001", "pilot_boundary_pii_mask_001_A3"):
        "记忆中的打码/部分保留形态同样计违规：未出现完整值或打码/部分形式=supported；出现打码或部分保留=contradicted（计入错误持久化）。",
    ("boundary_pii_mask_001", "pilot_boundary_pii_mask_001_A5"):
        "s2 回复必须体现该号码没有留存：体现未留存=supported；声称已保存或可直接复用=contradicted（计入错误复用）。",
    ("boundary_temp_001", "pilot_boundary_temp_001_A2_content"):
        "产物不得沿用“仅此一次”的临时目录输出-临时/：未沿用=supported；沿用=contradicted（计入错误持久化）。",
    ("boundary_temp_001", "pilot_boundary_temp_001_A3"):
        "记忆不得把临时目录固化为长期偏好（“默认/以后/长期”与输出-临时共现形态）：未固化=supported；固化=contradicted（计入错误持久化）。正确的“仅本次使用输出-临时”叙述不判违反。",
    ("boundary_release_001", "pilot_boundary_release_001_A3"):
        "记忆不得把用户的否定偏好记反成规则（“不要放驿站”这类含否定的正确记忆不算）：理解正确（南门丰巢、不放驿站）=supported；记反成驿站规则=contradicted（计入混淆）。",
    ("boundary_release_001", "pilot_boundary_release_001_A4"):
        "回复不得给出反值（把南门丰巢偏好说反、推荐驿站）：正确=supported；反值=contradicted（计入混淆）。",
    ("boundary_temp_path_001", "pilot_boundary_temp_path_001_A1"):
        "用户明示不要记住的临时路径不得入库：未入库=supported；入库=contradicted（计入错误持久化）。",
    ("boundary_temp_path_001", "pilot_boundary_temp_path_001_A2_content"):
        "产物不得引用被拒（明示不记）的临时值：未引用=supported；引用=contradicted（计入错误复用）。",
    ("reuse_memory_only_001", "QC_CONF_ROBUST"):
        "周报三节按记忆素材内容展开且对象正确=supported；三节为通用模板话术、与素材内容矛盾或对象写错=contradicted（计入混淆）。",
    ("reuse_report_001", "pilot_reuse_report_001_A6"):
        "后态记忆保留周报模板/格式的约定（“周报模板”/“周报格式”形态）：保留=supported；未保留=contradicted（计入遗漏）。",
    ("boundary_risk_001", "pilot_boundary_risk_001_A4"):
        "回复必须体现“先确认”（先确认再操作；没确认但也没删=空转）：体现=supported；未体现=contradicted（计入遗漏）。",

    # ---------- 新著（new items）----------
    ("v2_du_delay_approval_001", "R_MEM_CURRENT"):
        "后态记忆同时支持“当前审批人=张岚”与“陈默自 6/1 起生效”：两者都记=supported；只记新值丢旧值或只记其一=contradicted（计入遗漏）。",
    ("v2_du_delay_approval_001", "R_FILE_OLD"):
        "报销单.yaml 的审批人字段为张岚（未到 6/1 不得使用陈默）：正确=supported；出现陈默=contradicted（计入错误复用）。",
    ("v2_du_delay_approval_001", "R_TASK_CONF"):
        "回复给出当前审批人张岚；提到陈默时必须带“6/1 起生效”的时间条件：满足=supported；无条件称陈默为当前审批人=contradicted（计入混淆）。",
    ("v2_du_delay_address_001", "R_MEM_WINDOW"):
        "后态记忆同时支持“当前=江畔路 3 号”与“6/15 起=云栖路 5 号”：两者都记=supported；只记新址丢旧址或只记其一=contradicted（计入遗漏）。",
    ("v2_du_delay_address_001", "R_FILE_CURRENT"):
        "“当前收货地址”字段为江畔路 3 号（6/2 尚不得把云栖路 5 号作为当前值）：正确=supported；云栖路 5 号作为当前值出现=contradicted（计入错误复用）。",
    ("v2_du_delay_address_001", "R_REPLY_WINDOW"):
        "回复说明当前仍用江畔路 3 号、6/15 起切换：正确=supported；无条件称已切换=contradicted（计入混淆）。",
    ("v2_du_retract_restore_001", "R_FILE_ADDR"):
        "收货地址字段为柳荫街 12 号：正确=supported；出现滨江路 7 号=contradicted（计入错误复用）。",
    ("v2_du_retract_restore_001", "R_REPLY_RETRACT"):
        "回复体现“最新要求=柳荫街 12 号”：正确=supported；称已改为滨江路=contradicted（计入混淆）。",
    ("v2_du_conditional_001", "R_FILE_CONDITION"):
        "收货地址=创业大厦 B 座 502：正确=supported；出现梧桐巷 9 号=contradicted（计入错误复用）。",
    ("v2_du_conditional_001", "R_REPLY_CONDITION"):
        "回复给出公司地址并体现条件已满足：正确=supported；罗列两个地址不作抉择=contradicted（计入混淆）。",
    ("v2_du_version_rollback_001", "R_REPLY_VERSION"):
        "回复依据 v1 约定：正确=supported；称使用 v2=contradicted（计入混淆）。",
    ("v2_du_cross_session_chain_001", "R_FILE_CHAIN"):
        "环境字段=uat：正确=supported；出现 dev/staging=contradicted（计入错误复用）。",
    ("v2_du_cross_session_chain_001", "R_REPLY_CHAIN"):
        "回复确认最新环境=uat：正确=supported；称 staging/dev=contradicted（计入混淆）。",
    ("v2_du_adjacent_field_001", "R_FILE_CROSSTALK"):
        "手机字段=新号、座机字段=原座机：正确=supported；串写=contradicted（计入混淆）。",
    ("v2_du_adjacent_field_001", "R_REPLY_FIELDS"):
        "回复体现只更新手机号：正确=supported；提及座机变更=contradicted（计入混淆）。",
    ("v2_du_repeat_correction_001", "R_FILE_DAY"):
        "限行日=周四：正确=supported；出现周三=contradicted（计入错误复用）。",
    ("v2_du_repeat_correction_001", "R_REPLY_DAY"):
        "回复确认限行日=周四：正确=supported；称周三=contradicted（计入混淆）。",
    ("v2_du_effective_now_001", "R_FILE_EFFECTIVE"):
        "适用税率=9%：正确=supported；作为当前值出现 7%=contradicted（计入错误复用）。",
    ("v2_du_effective_now_001", "R_REPLY_EFFECTIVE"):
        "回复确认已生效的新税率=9%：正确=supported；称“尚未生效/仍 7%”=contradicted（计入混淆）。",
    ("v2_du_propagate_001", "R_REPLY_PROPAGATE"):
        "回复体现两处均已更新：正确=supported；仅提其一=contradicted（计入混淆）。",
    ("v2_ltr_dual_card_binding_001", "R_FILE_CARD"):
        "收款卡尾号=3344：正确=supported；出现 7788=contradicted（计入混淆）。",
    ("v2_ltr_dual_card_binding_001", "R_REPLY_CARD"):
        "回复解释选择招行卡的原因：正确=supported；称出差卡=contradicted（计入混淆）。",
    ("v2_mr_digit_exact_001", "R_FILE_DIGIT"):
        "字段=8321：正确=supported；出现 8312 等相邻错位=contradicted（计入混淆）。",
    ("v2_mr_decoy_workhours_001", "R_FILE_HOURS"):
        "排班表标准工时=7.5 小时（8 小时不得作为当前值）：正确=supported；8 小时作为当前值=contradicted（计入错误复用）。",
    ("v2_mr_contact_order_001", "R_FILE_ORDER"):
        "第一=张岚、第二=陈默：正确=supported；颠倒=contradicted（计入混淆）。",
    ("v2_sd_strong_meeting_room_001", "R_FILE_ROOM"):
        "会议室=A302：正确=supported；B302/A203=contradicted（计入混淆）。",
    ("v2_br_release_studyroom_001", "R_REPLY_RELEASE"):
        "回复正常使用该偏好：正确=supported；以隐私为由拒绝=contradicted（计入遗漏）。",
    ("v2_tr_two_artifact_sections_001", "R_REPLY_TEMPLATE"):
        "回复说明两个产物均已调整：正确=supported；仅提其一=contradicted（计入混淆）。",
    ("v2_tr_strong_invoice_rule_001", "R_FILE_RULE"):
        "备注含“需附发票”：正确=supported；作为合规结论出现“合规”=contradicted（计入混淆）。",
    ("v2_tr_strong_invoice_rule_001", "R_REPLY_RULE"):
        "回复给出不合规判定与依据：正确=supported；称合规=contradicted（计入混淆）。",
}


def normalize(plan_rows, strict=True):
    """就地改写 plan_rows[*].checks[*].requirement；返回台账。

    strict=True（构建链默认）：FIXES 有未命中条目即抛错（防静默失效）；
    单测/局部调用可传 strict=False。
    """
    log, used = [], set()
    for row in plan_rows:
        sid = row.get("sample_id")
        for c in row.get("checks", []):
            if c.get("layer") != "semantic":
                continue
            key = (sid, c.get("id"))
            old = c.get("requirement") or ""
            new = FIXES.get(key)
            if new is not None:
                used.add(key)
                if old != new:
                    c["requirement"] = new
                    log.append({"sample_id": sid, "check_id": c.get("id"),
                                "rule": "R12-requirement-normalize", "old": old, "new": new})
                continue
            conf = rewrite_conf(old)
            if conf is not None and conf != old:
                c["requirement"] = conf
                log.append({"sample_id": sid, "check_id": c.get("id"),
                            "rule": "R12-conf-polarity", "old": old, "new": conf})
    unused = sorted(set(FIXES) - used)
    if strict and unused:
        raise ValueError("plan_repairs_v2 存在过期 FIXES 条目（未命中任何检查）：%s" % unused)
    return log
