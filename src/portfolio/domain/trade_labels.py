LINK_LABELS = {'LINKED': '已关联', 'LEGACY_UNLINKED': '历史未关联',
               'REVIEW_REQUIRED': '待审核', 'NOT_APPLICABLE': '不适用'}

LINK_HELP = {
    'LINKED': '人员操作已对应到券商真实成交。仅表示对应关系，不代表收益或所有数据都已核验。',
    'LEGACY_UNLINKED': '历史导入的人员操作尚无可靠券商成交对应关系；仍按有效人员账本参与回放，不代表交易无效。',
    'REVIEW_REQUIRED': '成交关联或人员归属尚待确认，请核对原始交易；相关数量可能仍为候选值。',
    'NOT_APPLICABLE': '这是券商真实成交账本，无需再关联另一条券商成交记录。',
}
