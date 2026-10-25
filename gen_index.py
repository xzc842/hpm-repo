#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动生成 index.json

用法：
    cd <repo-root>
    python3 gen_index.py

读取：
    domain.yaml           域配置（可选）

扫描：
    <developer>/<name>-<version>.tar.gz
    <developer>/<name>-<version>.tar.gz.sig
    <developer>/<name>.pub
    <developer>/<name>.pub.sig

生成：
    index.json（不签名）

说明：
    - chip 包：含 chip.json
    - driver 包：含 manifest.json
    - 版本从文件名解析
    - sha256 / size 自动计算
    - url / sig_url 用 {baseurl} 占位符
    - requires 从 manifest.json 读
    - kind 从包内容判断
    - domain.yaml 里的域配置写入 index.json 的 domains 字段
"""

import hashlib
import json
import sys
import tarfile
from pathlib import Path

# 尝试导入 yaml（PyYAML）
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# 仓库根目录
REPO_DIR = Path(__file__).resolve().parent

# 忽略的目录
IGNORE_DIRS = {".git", "publish", "__pycache__", ".github", "toolchains"}

# 忽略的文件
IGNORE_FILES = {"index.json", "index.json.sig", "gen_index.py",
                "README.md", "LICENSE", "domain.yaml"}

# 忽略的扩展名
IGNORE_SUFFIXES = {".key"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def read_package_meta(tar_path):
    """
    读 tar.gz 里的 chip.json 或 manifest.json
    返回 (kind, name, requires)
    """
    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                parts = member.name.split("/")
                if len(parts) < 2:
                    continue
                fname = parts[-1]
                if fname not in ("chip.json", "manifest.json"):
                    continue

                f = tar.extractfile(member)
                if not f:
                    continue
                data = json.loads(f.read().decode("utf-8"))

                if fname == "chip.json":
                    return ("chip", data.get("name", ""), {})
                else:
                    reqs = {}
                    for key, req in data.get("requires", {}).items():
                        reqs[key] = {"type": req.get("type", "")}
                    return ("driver", data.get("name", ""), reqs)
    except Exception as e:
        print(f"  warn: 读 {tar_path} 失败: {e}")
    return (None, None, None)


def parse_filename(fname):
    """
    从 <name>-<version>.tar.gz 解析 name 和 version
    name 里可能有 '-'，version 是最后一段 semver
    """
    if not fname.endswith(".tar.gz"):
        return None, None

    base = fname[:-len(".tar.gz")]
    parts = base.rsplit("-", 1)
    if len(parts) != 2:
        return None, None

    name, ver = parts
    vparts = ver.split(".")
    if len(vparts) < 2:
        return None, None
    for p in vparts:
        if not p.isdigit():
            return None, None

    return name, ver


def load_domain_config():
    """
    读 domain.yaml
    返回 dict: {domain_name: {index, official_pub, official_pub_sig}}
    """
    path = REPO_DIR / "domain.yaml"
    if not path.exists():
        print("  domain.yaml 不存在，跳过")
        return {}

    if not HAS_YAML:
        print("  warn: 没装 PyYAML，跳过 domain.yaml")
        print("        安装：pip3 install pyyaml")
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  warn: 读 domain.yaml 失败: {e}")
        return {}

    domains = data.get("domains", {})
    if not isinstance(domains, dict):
        print("  warn: domain.yaml 的 domains 字段必须是对象")
        return {}

    result = {}
    for name, info in domains.items():
        if not isinstance(info, dict):
            print(f"  warn: 域 {name} 配置必须是对象")
            continue

        entry = {
            "index": info.get("index", ""),
            "official_pub": info.get("official_pub", ""),
            "official_pub_sig": info.get("official_pub_sig", ""),
        }

        if not entry["index"]:
            print(f"  warn: 域 {name} 缺少 index 字段")
            continue
        if not entry["official_pub"]:
            print(f"  warn: 域 {name} 缺少 official_pub 字段")
            continue
        if not entry["official_pub_sig"]:
            print(f"  warn: 域 {name} 缺少 official_pub_sig 字段")
            continue

        result[name] = entry

    return result


def main():
    repo = REPO_DIR
    print(f"仓库目录: {repo}")

    # 1. 读 domain.yaml
    print(f"\n读 domain.yaml")
    domains = load_domain_config()
    if domains:
        for name in domains:
            print(f"  ok: {name}")
    else:
        print(f"  （无域配置）")

    # 2. 扫描开发者目录
    developers = {}
    packages = {}

    for entry in sorted(repo.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in IGNORE_DIRS:
            continue

        dev_name = entry.name
        print(f"\n开发者: {dev_name}")

        # 公钥
        pub = entry / f"{dev_name}.pub"
        pub_sig = entry / f"{dev_name}.pub.sig"

        if not pub.exists():
            print(f"  warn: 缺少公钥 {pub}")
            continue
        if not pub_sig.exists():
            print(f"  warn: 缺少公钥签名 {pub_sig}")
            continue

        developers[dev_name] = {
            "pub": f"{{baseurl}}/{dev_name}/{dev_name}.pub",
            "pub_sig": f"{{baseurl}}/{dev_name}/{dev_name}.pub.sig",
        }
        print(f"  ok: 公钥 + 签名")

        # 包
        for tar in sorted(entry.glob("*.tar.gz")):
            if tar.name.endswith(".sig"):
                continue

            name, ver = parse_filename(tar.name)
            if not name or not ver:
                print(f"  warn: 跳过（文件名不符合）: {tar.name}")
                continue

            sig = entry / (tar.name + ".sig")
            if not sig.exists():
                print(f"  warn: 缺少签名: {sig.name}")
                continue

            kind, pkg_name, requires = read_package_meta(tar)
            if not kind:
                print(f"  warn: 跳过（无法识别包类型）: {tar.name}")
                continue

            sha = sha256_file(tar)
            size = tar.stat().st_size

            if name not in packages:
                packages[name] = {"versions": {}}

            packages[name]["versions"][ver] = {
                "kind": kind,
                "developer": dev_name,
                "url": f"{{baseurl}}/{dev_name}/{tar.name}",
                "sig_url": f"{{baseurl}}/{dev_name}/{tar.name}.sig",
                "sha256": sha,
                "size": size,
                "license": "MIT",
                "requires": requires,
            }
            print(f"  ok: {name}@{ver} ({kind}, {size} bytes)")

    # 3. 生成 index.json
    index = {
        "schema": 1,
        "official_pub": "{baseurl}/official.pub",
        "developers": developers,
        "domains": domains,
        "packages": packages,
    }

    index_path = repo / "index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\n=== 完成 ===")
    print(f"开发者: {len(developers)}")
    print(f"域: {len(domains)}")
    print(f"包: {len(packages)}")
    print(f"索引: {index_path}")

    # 4. 提示重新签名
    print(f"\n下一步：")
    print(f"  # RSA")
    print(f"  openssl dgst -sha256 -sign official.key -out index.json.sig index.json")
    print(f"  # Ed25519")
    print(f"  openssl pkeyutl -sign -inkey official.key -rawin -in index.json -out index.json.sig")


if __name__ == "__main__":
    main()