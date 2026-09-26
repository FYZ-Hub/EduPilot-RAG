# 生成器字体来源与许可证

生成 PDF 时必须嵌入中文字体。为避免依赖宿主机（尤指 Windows）系统字体、也避免在
生成阶段联网下载，本目录提交了唯一的固定字体文件，生成器只读取该文件。

## 字体

- 名称：Noto Sans SC（中文黑体，含 `wght` 可变轴）
- 文件：`NotoSansSC-VF.ttf`
- 大小：17,772,300 字节
- SHA-256：`a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da`

## 许可证

- SIL Open Font License 1.1（见同目录 `OFL.txt`）
- OFL.txt 的 SHA-256：`1c05c68c34f9708415aada51f17e1b0092d2cea709bf4a94cd38114f9e73d7d9`
- Copyright：2014-2021 Adobe，保留字体名称 'Source'

## 来源

- 上游仓库路径：`google/fonts` → `ofl/notosanssc/NotoSansSC[wght].ttf`
- 下载地址：`https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf`
- 许可证地址：`https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/notosanssc/OFL.txt`

该文件在开发阶段一次性下载后提交入库；`scripts/generate_demo_corpus.py` 运行时
只读取本目录文件，不访问网络、不下载字体、也不回退到系统字体。
