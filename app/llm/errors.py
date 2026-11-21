"""把供应商的报错翻成观众和馆方都能看懂的中文，不把原始调试信息读进回答。"""


class ModelCallError(Exception):
    def __init__(self, notice: str):
        super().__init__(notice)
        self.notice = notice


def notice_for_http(status_code: int, body: str, service: str) -> str:
    if status_code in {401, 403}:
        return f"{service}拒绝了当前密钥或权限。请检查 .env 里的 API Key，本次没有生成人物回答。"
    if status_code == 402:
        return f"{service}余额不足。请充值后再试，本次没有生成人物回答。"
    if status_code == 429:
        return f"{service}暂时繁忙或超出调用额度。请稍后再试，本次没有生成人物回答。"
    if status_code >= 500:
        return f"{service}暂时不可用（服务端错误）。请稍后再试，本次没有生成人物回答。"
    snippet = " ".join((body or "").split())[:180]
    detail = f"详情：{snippet}" if snippet else "没有更多详情。"
    return f"{service}返回了错误（状态码 {status_code}）。{detail}本次没有生成人物回答。"
