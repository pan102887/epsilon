"""网关/Sidecar 统一 HTTP 客户端模块。

提供对集群内部服务的统一 HTTP 访问能力，封装 base_url、超时、
连接池等横切关注点，供各业务 Adapter 复用。
"""
