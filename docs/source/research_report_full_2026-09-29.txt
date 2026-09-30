# 同步多模态训练的跨阶段物理优化：反证优先审计

检索与核验截止：2026-09-29。范围：公开论文、附录、作者 artifact、官方源码与生产技术报告。**本次没有运行 GPU 实验，没有独立复现论文加速比，也没有获得逐样本跨阶段生产 trace。**

本报告回答三个问题：现有工作是否已经跨越 preprocessing / training 边界；真实在线工作负载是否仍保留可优化的物理选择；强机制之后是否存在值得投入的可重复 regret。状态统一为 **FACT**（来源直接支持；性能为作者报告）、**INFERENCE**（本报告推导）、**HYPOTHESIS**（待实验）、**UNKNOWN**（证据不足）。

## 1. Executive Verdict

**INVESTIGATE**

当前不能给 GO。宽泛的“需要联合考虑预处理和训练”已经被多项 prior art 覆盖；尚未找到公开实验证据，证明强机制组合之后仍有 ≥15–20% 的 joint physical-plan regret。

**最可信、但尚未成立的狭窄 gap：**在固定每次 optimizer update 的样本、增强和损失语义之后，有限内存下的视频解码/表示转换计划可能改变不同微批的就绪时间与资源占用，使训练成本均衡后的调度反复选错输入物理计划；值得测量的是这种“计划排名反转”是否在简单预取、紧凑表示、分离部署和双成本均衡之后仍然存在。

**最大反方证据：**RAP 已直接联合输入图映射、fusion 与训练共执行；FusionFlow 已协调 GPU 预处理竞争与多 GPU 供数；DistTrain 将其测量场景中的预处理开销从秒级降到毫秒级；新版 MegaScale-Data 已有训练成本、内存与拓扑感知的数据编排。把这些机制重新组合，不能自动形成贡献。[RAP §7](https://storage.googleapis.com/yuke_profile/ASPLOS24_RAP.pdf)、[DistTrain §5、§7](https://arxiv.org/html/2408.04275v3)、[MegaScale-Data v4 §4–5](https://arxiv.org/html/2504.09844v4)。

**最大未知量：**同一真实视频 workload、同一预算、同一 Legal(P) 下，strong baseline 相对可执行 joint reference 的剩余差距。公开论文没有提供可直接计算这个量的共同实验。

建议只批准一次 measurement-only E0。不要先实现通用 optimizer，不要把 fusion、分布模型或物化预先写成贡献。若 E0 的大多数自然配置差距 <5–10%，应停止这个具体候选问题。

## 2. Confirmed Facts

以下事实不等于“候选 gap 已成立”。

| 直接支持的事实 | 证据与边界 |
|---|---|
| 已有输入物理决策与训练执行的联合优化 | RAP §7.2 明确联合 preprocessing graph mapping 与 co-run schedule；适用 DLRM，不直接证明视频 sample regrouping 已解决。[原文](https://storage.googleapis.com/yuke_profile/ASPLOS24_RAP.pdf) |
| 已有算子重排和混合远程/本地预处理 | Pecan 的核心就是 transformation ordering 与 hybrid placement；其 relaxed commutativity 不能当作严格梯度等价。[原文](https://www.usenix.org/system/files/atc24-graur.pdf) |
| 训练数据编排不只关心样本长度排序 | MegaScale-Data v4 提供 `cost(meta)`、SampleGraph/ClientPlaceTree 和跨模块 balancing，另有资源自动扩缩；自动 transformation placement/rewrite 的边界要按该版本 §9 判断。[原文](https://arxiv.org/html/2504.09844v4) |
| encoder 与 LLM 的联合计划/调度已是密集研究区 | DFLOP、MegaScale-Omni、Optimus、DIP 分别处理并行配置、微批、模块异质与 bubble；不能把训练中的 vision encoder 改名为 preprocessing 来制造新 gap。[DFLOP](https://arxiv.org/html/2603.25120v1)、[Omni](https://arxiv.org/html/2605.08962v1)、[Optimus](https://www.usenix.org/system/files/atc25-feng.pdf)、[DIP](https://arxiv.org/html/2504.14145v2) |
| 视频物化、内存约束和需求优先级已被结合 | SAND 构造具体对象依赖图，在存储限制下剪枝，并按训练需求及内存压力调度；不是只会预取原始文件。[§5](https://ina.kaist.ac.kr/assets/bibliography/SAND.pdf) |
| 工业多模态训练仍存在在线 transformation | 字节 OSDI26 工作的 §5 分析生产长尾，并部署存储侧 JIT transformation、预取和回退；这既支持问题存在，也提供强反方 baseline。[原文](https://www.usenix.org/system/files/osdi26-chen-luofan.pdf) |
| 离线预处理与在线变化可以共存 | Cosmos 的 curation 规范化视频格式；Qwen2.5-VL 使用动态分辨率/FPS，并按 ViT/LLM 负载组织训练数据。[Cosmos](https://arxiv.org/html/2501.03575v1)、[Qwen2.5-VL](https://arxiv.org/html/2502.13923v1) |
| 动态 mixture 已有专门 data plane | Mixtera 支持按属性声明、动态调整与可复现的数据供给；改变 mixture 属于训练策略契约，不是任意物理自由度。[最终论文 §2–4](https://anakli.inf.ethz.ch/papers/mixtera_sigmod2026.pdf) |

## 3. Prior-art Coverage Matrix

### 3.1 判定口径

**YES**：有直接机制证据；仍需看 cell 说明它是自动搜索、固定策略还是用户启用的功能。**PARTIAL**：只覆盖部分层次/固定分工/相关物理量，不能据此声称任意 operator 的自动优化。**NO**：仅指已审计论文/源码中明确界定的优化空间排除此变量，或明确列为未来工作。**UNKNOWN**：未取得足够证据；不是 NO。

每个 cell 的机制、原文章节/页码、代码路径与证据强度见 [逐 cell 证据矩阵](./coverage_matrix.md) 和 [机器可读矩阵](./coverage_matrix.json)。代码证明实现存在，不证明默认启用，更不证明实验效果。没有公开可核验代码的系统保留 paper-only 状态。

| Decision / Property | cedar | Pecan | FusionFlow | MinatoLoader | Plumber |
|---|---|---|---|---|---|
| logical operator DAG | YES | YES | PARTIAL | PARTIAL | YES |
| operator reorder | YES | YES | NO | NO | UNKNOWN |
| fusion | YES | PARTIAL | UNKNOWN | UNKNOWN | PARTIAL |
| materialization / pipeline breaking | YES | PARTIAL | PARTIAL | PARTIAL | YES |
| CPU/GPU placement | PARTIAL | NO | YES | NO | NO |
| remote placement | YES | YES | PARTIAL | UNKNOWN | UNKNOWN |
| operator parallelism | YES | PARTIAL | PARTIAL | PARTIAL | YES |
| sample cost model | PARTIAL | PARTIAL | PARTIAL | YES | PARTIAL |
| latency distribution | NO | NO | PARTIAL | YES | NO |
| intermediate size | YES | YES | PARTIAL | PARTIAL | YES |
| memory / workspace | PARTIAL | PARTIAL | YES | PARTIAL | YES |
| batching | PARTIAL | PARTIAL | YES | YES | PARTIAL |
| sample reorder | PARTIAL | UNKNOWN | PARTIAL | YES | UNKNOWN |
| microbatch scheduling | NO | NO | NO | NO | NO |
| DP/PP/TP awareness | PARTIAL | PARTIAL | PARTIAL | PARTIAL | UNKNOWN |
| training barrier objective | PARTIAL | PARTIAL | YES | PARTIAL | PARTIAL |
| online plan adaptation | YES | YES | YES | PARTIAL | PARTIAL |

| Decision / Property | PRESTO | Seneca | Cachew | CoorDL | tf.data |
|---|---|---|---|---|---|
| logical operator DAG | PARTIAL | PARTIAL | YES | PARTIAL | YES |
| operator reorder | UNKNOWN | NO | UNKNOWN | UNKNOWN | PARTIAL |
| fusion | UNKNOWN | UNKNOWN | PARTIAL | UNKNOWN | YES |
| materialization / pipeline breaking | YES | YES | YES | PARTIAL | PARTIAL |
| CPU/GPU placement | UNKNOWN | PARTIAL | NO | PARTIAL | PARTIAL |
| remote placement | PARTIAL | PARTIAL | YES | PARTIAL | PARTIAL |
| operator parallelism | PARTIAL | UNKNOWN | PARTIAL | UNKNOWN | YES |
| sample cost model | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| latency distribution | UNKNOWN | NO | NO | UNKNOWN | PARTIAL |
| intermediate size | YES | YES | YES | PARTIAL | PARTIAL |
| memory / workspace | PARTIAL | YES | PARTIAL | PARTIAL | YES |
| batching | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| sample reorder | UNKNOWN | YES | UNKNOWN | PARTIAL | PARTIAL |
| microbatch scheduling | NO | UNKNOWN | NO | UNKNOWN | UNKNOWN |
| DP/PP/TP awareness | NO | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| training barrier objective | NO | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| online plan adaptation | NO | PARTIAL | YES | PARTIAL | YES |

| Decision / Property | tf.data service | NVIDIA DALI | DistTrain | MegaScale-Data | MegaScale-Omni |
|---|---|---|---|---|---|
| logical operator DAG | YES | YES | PARTIAL | PARTIAL | PARTIAL |
| operator reorder | PARTIAL | UNKNOWN | NO | NO | UNKNOWN |
| fusion | PARTIAL | PARTIAL | UNKNOWN | NO | PARTIAL |
| materialization / pipeline breaking | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| CPU/GPU placement | PARTIAL | PARTIAL | PARTIAL | NO | PARTIAL |
| remote placement | YES | UNKNOWN | YES | YES | PARTIAL |
| operator parallelism | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| sample cost model | PARTIAL | UNKNOWN | YES | YES | YES |
| latency distribution | UNKNOWN | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| intermediate size | PARTIAL | PARTIAL | PARTIAL | PARTIAL | YES |
| memory / workspace | PARTIAL | YES | YES | YES | YES |
| batching | YES | YES | YES | YES | YES |
| sample reorder | YES | PARTIAL | YES | YES | YES |
| microbatch scheduling | PARTIAL | UNKNOWN | YES | YES | YES |
| DP/PP/TP awareness | PARTIAL | PARTIAL | YES | YES | YES |
| training barrier objective | YES | UNKNOWN | YES | PARTIAL | YES |
| online plan adaptation | PARTIAL | PARTIAL | PARTIAL | YES | PARTIAL |

| Decision / Property | DFLOP | Trident | Ray Data | Data-Juicer 2.0 | RAP |
|---|---|---|---|---|---|
| logical operator DAG | NO | PARTIAL | YES | PARTIAL | YES |
| operator reorder | NO | NO | PARTIAL | YES | PARTIAL |
| fusion | NO | UNKNOWN | YES | YES | YES |
| materialization / pipeline breaking | NO | PARTIAL | PARTIAL | PARTIAL | PARTIAL |
| CPU/GPU placement | PARTIAL | YES | PARTIAL | PARTIAL | PARTIAL |
| remote placement | UNKNOWN | YES | YES | PARTIAL | PARTIAL |
| operator parallelism | PARTIAL | YES | PARTIAL | YES | PARTIAL |
| sample cost model | YES | PARTIAL | UNKNOWN | PARTIAL | PARTIAL |
| latency distribution | PARTIAL | PARTIAL | UNKNOWN | UNKNOWN | UNKNOWN |
| intermediate size | PARTIAL | YES | YES | PARTIAL | PARTIAL |
| memory / workspace | YES | YES | YES | YES | PARTIAL |
| batching | YES | YES | PARTIAL | YES | PARTIAL |
| sample reorder | YES | UNKNOWN | PARTIAL | UNKNOWN | UNKNOWN |
| microbatch scheduling | YES | NO | UNKNOWN | UNKNOWN | PARTIAL |
| DP/PP/TP awareness | YES | NO | PARTIAL | UNKNOWN | PARTIAL |
| training barrier objective | YES | NO | PARTIAL | NO | YES |
| online plan adaptation | PARTIAL | YES | PARTIAL | PARTIAL | PARTIAL |


### 3.2 是否已有系统同时优化两侧？

需要区分三种问题，而不能给一个含糊的“没有”。

1. **输入预处理与训练共执行是否已联合优化？是。** RAP 是直接反例，FusionFlow 也处理训练资源竞争与同步供数。
2. **预处理服务与训练 sample/microbatch 编排是否在一个系统内共存？是。** DistTrain 与 MegaScale-Data 已跨越这个工程边界；同系统共存并不自动表示一个统一目标函数搜索全部决策。
3. **是否已核验一个面向视频、同时搜索输入算子顺序/实现/物化位置与 DP/PP 微批执行、并显式预测 release-time 和共享资源影响的 optimizer？本次没有确认。** 这是有范围的检索结果，不能升级为“尚无人研究”，更不能代替 residual 实验。

候选未被本次证据完整覆盖的组合是：**输入物理替代计划产生的微批 release-time / live-byte / resource-demand 属性，进入严格语义下的训练 makespan 评估**。这只是检索边界；如果一个双成本 greedy 或小规模联合调参已消除差距，就没有必要做新的 DB optimizer。

### 3.3 必须补入原清单的反方工作

| 工作 | 对候选方向的压力 | 审计程度 |
|---|---|---|
| RAP，ASPLOS24 | 输入 DAG 映射、horizontal fusion 与训练层共执行直接联合；最强宽泛 novelty 反例 | 全文与 planner/scheduler 源码 |
| SAND，SOSP25 | 视频对象 DAG、物化剪枝、需求期限和内存压力联动 | 全文；未确认可审计作者实现 |
| HyCache，ATC25 | 多级、部分物化选择与 workspace/prefetch 内存预算 | 全文机制核验；代码 UNKNOWN |
| Optimus，ATC25 | encoder kernel 填补 LLM bubble | 全文关键算法与限制 |
| DIP，ASPLOS26 | 动态模态 sub-microbatch、调度搜索、内存策略 | 全文 HTML v2 |
| DynaPipe，EuroSys24 | 输入长度驱动的可变微批与 pipeline 执行 | 原文与官方 artifact 定位；详见 ledger |
| DISTMM，NSDI24 | 模态并行、batch balancing、跨样本 loss 同步 | 全文关键机制 |
| Mixtera，SIGMOD26 | 动态 mixture / order / reproducibility 的数据契约 | 最终论文；源码访问状态见 ledger |
| Teaching the Old Dog New Tricks，OSDI26 | 真生产视频在线长尾及存储侧 JIT/offload；不能只引用优化前数字 | 全文 §5–6 |
| Youmu，MLSys25；FFCV，CVPR23；GoldMiner，SIGMOD23；BWARE，VLDB26 program | 数据格式、离线表示、弹性分离和跨变换压缩分别侵蚀简单子问题 | 扩展筛查；未全部进行同深度源码审计 |

扩展来源：[Youmu](https://proceedings.mlsys.org/paper_files/paper/2025/hash/136b9a13861308c8948cd308ccd02658-Abstract-Conference.html)、[FFCV](https://github.com/libffcv/ffcv/)、[GoldMiner 官方会议信息](https://2023.sigmodconf.hosting.acm.org/sigmod_industrial_list.shtml)、[BWARE 作者论文](https://baunsgaard.github.io/assets/pdf/BWARE.pdf)。

检索覆盖用户列出的各会议与 arXiv，但**不是 2024–2026 所有 proceedings 的逐篇全量 census**。ICDE、CIDR 的相关新命中较弱，不能由此推断不存在近邻；详细审计完成度与未取得材料必须随结论一起保留。

## 4. Workload Evidence

### 4.1 哪些算子还在线？

“typically” 是对已核验工作流的归纳，未做工业采用率统计。offline 文件 curation、JIT 输入处理与模型 forward 必须分层：可训练 vision encoder 属于模型执行；固定 encoder 的特征只有满足版本与输入契约才能缓存。

| Operator | 分类 | 在线/离线边界与原因 |
|---|---|---|
| video decode | workload-dependent；压缩视频供数时 online/JIT | 全量 decoded tensor 膨胀；离线可做格式/分辨率规范化，重复训练也可部分物化；不能把“离线处理过”当作“不再解码” |
| frame sampling | workload-dependent | 固定 clip/index 可离线；动态 FPS、随机时间窗、不同 epoch/任务采样需要 JIT 或预先生成可复现计划 |
| random seek | workload-dependent | 是访问执行行为；GOP/keyframe 依赖使其不同于简单 length proxy；连续预解码、clip 化可缓解 |
| resize | workload-dependent | 固定目标尺寸可物化；动态 resolution/patch budget 或多任务目标通常保留在线部分 |
| crop | 通常 online（随机训练 crop） | 固定 ROI 可离线；随机 crop 不能缓存一个结果冒充每次独立增强 |
| augmentation | 通常 online（随机部分） | 可确定 seed 后延迟执行；离线多 view 是存储、随机覆盖与重复使用的另一契约 |
| tokenization | workload-dependent，固定 text corpus 常 offline | tokenizer/version/template 更换需重算；多模态插入、chat template 和 packing 接口可能仍在线 |
| packing | workload-dependent；动态 batch/mixture 常 online | 可离线固定 packing，但动态长度、mixture 与资源分配使在线重组有价值；必须保留 attention/loss 边界 |
| multimodal alignment | workload-dependent | 数据清洗/时间戳对齐可离线；训练时选择时间窗仍需一致地索引 video/audio/action；模型 alignment loss 在线 |
| vision encoder | 训练参数时 online，冻结时条件可离线 | 参数变动使 cached feature 过期；冻结也不代表工业实际缓存，增强输入、位置编码、dtype 等仍须一致 |
| feature extraction | workload-dependent | 固定辅助模型 embedding/标签常 offline；更新中的主模型表示不可任意缓存 |
| filtering | 通常 offline curation；动态选择依赖 workload | 质量过滤/去重与训练时 curriculum/feedback selection 不同；后者不是普通无语义 filter |
| batching | 通常 online assembly，可预排计划 | 批成员、padding、token budget、rank 分配决定成本和训练语义 |
| mixture construction | workload-dependent | 固定比例可预先索引；动态混合由训练反馈驱动；Mixtera 已覆盖 data-plane 层 |

证据细节、生产与研究实现的区分见 [工作负载证据附录](./workload_evidence.md)。其中最有力的生产锚点是字节 OSDI26 §5.2–5.5；其“完整离线张量不可行”结论限定于该生产规模。它没有排除压缩 clip 规范化、部分物化或 frozen-feature 缓存。Cosmos 的格式规范化又说明：不能预设工业视频一定保留原始 codec 异质性。

### 4.2 六类 workload 的选择

| 类型 | 剩余 online 面 | 作为主 workload 的评价 |
|---|---|---|
| text-only LLM | 读取、shuffle、packing、mixture；tokenization 可能已离线 | 阴性对照更合适；输入 DAG 通常不足以支持本候选的复杂空间 |
| image training | decode/crop/augmentation，变形与缓存 | 易验证，但 Pecan/FusionFlow/DALI/FFCV/Minato/Seneca 密集；适合作为跨 workload 复核 |
| video-heavy MLLM | 压缩 decode/seek、选帧、动态分辨率、与 token/encoder workload 关联 | **E0 首选**；保留原始视频长度分布，不能仅取固定帧数小 clip 后仍声称长尾问题 |
| multimodal foundation model | 模态 mixture、encoder/LLM balancing、输入变换 | 很强 production 相关性，但强近邻最多且全规模验证昂贵；先固定拓扑 |
| embodied/robotics | 视频与 state/action 时间窗、跨视角同步、随机视觉增强 | 次选外部验证；若主瓶颈只是视频读/解码，不宜另造“embodied optimizer” |
| offline curation | decode、过滤、辅助推理、feature extraction、大中间数据 | Ray Data/Data-Juicer/Trident 是直接近邻；没有训练 step barrier，不能用它证明同步训练 gap |

**判断（INFERENCE）：**工业离线处理显著缩小 operator DAG，但没有普遍消灭在线视频输入。剩余空间大小取决于训练阶段、访问重复度、目标 resolution/frame policy 与存储预算；尚无可量化的全行业占比。

## 5. Coupling Mechanisms

### 5.1 可分性判据应改为“独立最优能否组合”

用户给出的加法检验是有用起点，但不是充分判据。假设可行集是笛卡尔积、资源无竞争，且流水线稳态成本为

\[
C(P_d,P_t)=\max\{C_d(P_d),C_t(P_t)\}.
\]

它不是加法，分别最小化两项却仍给出全局最优。更一般地，若成本是两个独立标量的单调函数，且各自最优计划不会损害另一侧，统一搜索并无结构性必要。

真正要测的是两个现象：

- **可行域耦合：**输入 workspace/队列占用使某个 training microbatch/parallel plan 不再可行。
- **计划排名反转：**存在合法 `d1,d2,t1,t2`，使 `C(d1,t1)<C(d2,t1)`，而 `C(d1,t2)>C(d2,t2)`。一个只输出平均 samples/s 的输入规划器无法表达这种条件。

即便发生反转，也只证明需要某种协调；固定两三条规则、交替优化或小网格搜索可能已经足够，不自动证明需要复杂 DB optimizer。

### 5.2 统一机制表达

令 `r_i(P_d,σ_d,q,R)` 为样本/微批在给定输入调度、队列及资源轨迹下的就绪时间。训练任务 `v` 的开始时刻满足：

\[
S_v=\max\{\max_{u\in pred(v)}F_u,\ r_v,\ A_{device(v)}\},\qquad
F_v=S_v+\tau_v(P_t,load(P_d),shape_v).
\]

共享预算约束：

\[
M_{model}(P_t)+M_{activation}(P_t,t)+M_{prep}(P_d,t)+M_{queue}(t)\le M_{total}.
\]

输入队列还有 live-byte 限制 `Σ_{i∈Q(t)} size_i ≤ M_Q`，而非仅 `len(Q)≤q`。同步 step 是该依赖图的 makespan，不能把逐 rank 等待时间直接相加。

### 5.3 十三种 coupling 的逐项判断

| 机制 | 数学/逻辑作用 | 真实证据与已有处理 | 局部最优是否可能失效；剩余判断 |
|---|---|---|---|
| 1. shared CPU/GPU contention | `τ_train` 与 input worker/thread 数相关；launcher/collective CPU 也受影响 | Pecan hybrid placement；FusionFlow；RAP | 可能；简单隔离/限核/需求驱动供数必须先赢过 |
| 2. GPU prep 与模型竞争 | `τ_train(gpu_prep)>τ_train(idle)`；SM/HBM/copy engine 占用不同 | RAP co-run model；FusionFlow；DALI 执行能力 | 确有机制；已覆盖很深，不能作为单独 gap；NVDEC 也不等于大量占用 SM |
| 3. intermediate expansion | `q_eff≈floor(M_Q/size)`，transfer=`size/BW` | cedar、SAND、HyCache、Trident；字节 JIT sampling before transfer | 可能；要证明 resize/uint8 等简单表示选择不能统一占优 |
| 4. bounded queues/backpressure | queue 反向改变 `r_i`；平均吞吐不决定 barrier tail | Ray Data backpressure、SAND 内存调度、MegaScale-Data buffers | 可能；大预取与byte-based credits是强反方 |
| 5. fusion 减少调度自由 | fused task 非抢占区间变长，关键微批不能提前释放 | RAP 按 overlap capacity 拆 fused kernels；cedar fusion；DALI fused ops | 理论可能；单次 startup 反例不能当稳态收益 |
| 6. materialization 增加内存/传输 | recompute 节省与 live-byte/网络/activation 争用互相制约 | PRESTO、Cachew、HyCache、SAND、Seneca | 已是成熟问题；要找到训练条件使缓存/切点排名变更的残差 |
| 7. sample reorder 双侧影响 | 优先完成 prep-heavy 样本可能形成 train-heavy rank；反之亦然 | Minato tail；DistTrain、MegaScale-Data/Omni 训练均衡 | 可能；向现有 greedy 加第二个成本维度是必须击败的 baseline |
| 8. batch composition 双侧影响 | vectorization/cache locality 与 padding/视觉 token 成本不同 | DFLOP、DynaPipe、MegaScale-Data，Qwen packing | 已覆盖训练半侧；不必有统计 residual 才产生物理冲突 |
| 9. microbatch composition | 改变 release max、workspace 和层效率 | DFLOP、DIP modality submicrobatch、Optimus | 主体空间被占据；输入释放约束的附加收益未知 |
| 10. DP rank sync | `T_step≈max_r T_r`；平均 prep 改善可能不降最慢 rank | FusionFlow DP 协调、tf.data service coordinated reads、DistTrain | 广义 DP-awareness 非新；局部 CPU/GPU 窗口与rank临界路径交互待测 |
| 11. PP bubble | deadline 是阶段开始时刻，早/晚到达代价不同 | DynaPipe、Optimus、DIP、MegaScale-Omni | bubble-aware scheduling 已有；高校 E0 不应先从大 PP 拓扑开始 |
| 12. dynamic modality mixture | 需求分布随 step 变化；prefetch/cache 投资可能过时 | Mixtera、MegaScale-Data、DIP、Trident 的 drift 控制 | 可能；变化频率与阶段驻留时间必须真实，不能人为每step翻转 |
| 13. migration/reconfiguration | 只有累计收益 `ΣΔT > T_profile+T_move+T_warm` 才有利 | Trident transition cost/rolling update、MegaScale-Data reshard | 可能；稳态固定计划经常更优，应计完整摊销 |

以上 paper/code 定位在 coverage annex 与 §10，表中“可能”均是 INFERENCE，未声称对应强 baseline 已失败。

### 5.4 最小 counterexample：用于识别机制，不能充当结果

**A. 共享 GPU：真实机制，已被 prior art 处理。**

假设 CPU 输入处理为 35 ms，GPU 输入处理为 12 ms；模型独占 100 ms，但 GPU 共执行时升至 116 ms。CPU 输入可以在独立资源上完全重叠，稳态 step 约 100 ms；盲选“更快的 GPU 输入”却使 step 至少 116 ms。这里每个量都是可测系统变量，比例不要求极端。但这些数字是**说明性假设，并非论文实测**；RAP/FusionFlow 正在处理此类情况，故它否定的是独立平均成本模型，不支持新论文。

**B. fusion / release-time：逻辑反例，必须检查是否被稳态重叠消除。**

同一 update 有两个固定微批，每个训练 40 ms。输入计划 A 使用整体处理，40 ms 时两批同时释放；B 分块处理，在 25、50 ms 释放。无跨 update 预取的这一窗口中，A makespan=120 ms，B=105 ms，尽管 A 的全部输入更早完成。若持续运行且 A 能预取下一窗口，这个优势可能消失；除非实测 byte budget、依赖或真实反馈边界导致这种等待反复暴露，不能将其算作 steady-state gap。

**C. 表示与队列：有真实尺寸基础，性能结论未知。**

16 帧、384×384、RGB，一个样本的 uint8 像素为 6.75 MiB，float32 为 27 MiB（纯尺寸计算，不含 decoder workspace）。在同一个 host queue budget 下，提前转 float32 将缓冲容量缩为约四分之一。若训练均衡策略需要较大的候选窗口，输入吞吐更高的提前转换方案可能导致窗口不足、关键微批迟到；延迟转换又可能竞争 GPU。**必须实测是否存在计划排名反转**；如果 uint8 传输加 GPU 转换在所有配置都占优，简单固定规则就解决了问题，应停止该假设。

因此：**联合协调存在结构性必要的情形已被证实；一个新的通用 joint optimizer 是否必要仍是 UNKNOWN。** 本次没有找到已独立复现、满足用户全部 strong-baseline 条件的具体视频反例。

### 5.5 Cross-stage cost correlation

**新增生产锚点：Cosmos 3。** 其 §5.2、§5.2.6 明确保留在线增强和 VAE 编码，并已有 rank-synchronous stream selection、有限 look-ahead、token budget、per-stream prefetch。当前公开代码另有基于校准形状成本的 iteration-time packing budget，**默认关闭**；不能从代码存在推断部署默认启用。这是必须击败的简单强基线。[技术报告](https://research.nvidia.com/labs/cosmos-lab/cosmos3/technical-report.pdf)、[cost model 固定快照](https://github.com/NVIDIA/cosmos-framework/blob/cf5d68c00d97ccd2480a2320ed652b92dec63102/cosmos_framework/utils/generator/cost_model/estimator.py)。


| 问题 | 审计结论 |
|---|---|
| `C_prep` 与 `C_train` 有多强相关？ | UNKNOWN。没有可据以给出统一 Pearson/Spearman/MI 数值的公开配对 trace。帧数/分辨率是可能的共同因子，codec/GOP/seek 可只影响输入 |
| 简单 metadata 是否够用？ | 训练侧已有很强正证据；输入侧不宜只用文件大小。但“单个 proxy 不够”不等于完整 metadata 模型不够 |
| 现有系统是否使用 proxy？ | DFLOP 使用形状/序列与 profile 成本，DistTrain 使用模态/sample size 等，Omni 使用数据长度/大小做均衡，MegaScale-Data 暴露 `cost(meta)`；需按各论文而非统一叫 length model |
| `I(C_prep;C_train | metadata)` 是否很大？ | UNKNOWN；未检得足够配对轨迹。不能从两份独立直方图、平均 stage time、或论文加速比推算 |

**这个 MI 不应是 GO 的必要条件。** 即便给定 metadata 后两边成本都确定、conditional MI 没有额外信息，有限队列和共享资源仍会造成计划冲突。反过来，两边时间残差可能仅由共享负载共同扰动，不能据此证明存在新的样本固有属性。

E0 要分别测：M0=帧数/文本 token/输出 resolution；M1=再加原始 bytes、duration、codec、GOP/seek、采样跨度；M2=再加缓存/队列/并发状态。按视频/源而非帧随机拆 train-test，比较交叉验证成本预测和**选错计划的实际 regret**。训练成本必须绑定实际 batch、mask、拓扑和 trainable/frozen 状态，不能将单样本 standalone 延迟机械相加。

## 6. Strongest Counterexamples Against This Direction

1. **DistTrain 型分离已把输入开销移出关键路径。** 该论文的结果是强反例，不能因为仍有 decode 就声称有性能空间；在 E0 中若强策略及少数有希望的替代训练策略都只剩 <5% GPU-ready headroom，可停止当前输入 optimizer 候选；单个固定 Pt 的对照不足以否定所有联合策略。[§7](https://arxiv.org/html/2408.04275v3)
2. **RAP/FusionFlow 已解决最直观的共享资源冲突。** “GPU preprocessing 抢训练”不能单独支撑 novelty；需比较它们的需求驱动/限额/共执行机制。
3. **offline curation、codec 规范化或 frozen-feature 缓存消掉多数选择。** 固定表示下仅剩加载和 packing 时，不需要通用 operator DAG 搜索；Cosmos 是规范化的实际反方，不只是可能性。
4. **metadata 足够，简单双成本均衡足够。** DFLOP/DistTrain/Omni/Data 已大量利用 shape/size；若加入 prep-time estimate 的 greedy 就达到 reference 的 95%以上，复杂优化器价值弱。
5. **目标虽非加法，但最优仍可分解。** 无共享瓶颈、预取足够、各 stage 独立单调时，`max(Cd,Ct)` 不需要全局搜索。这是数学反方，而非待查文献事实。
6. **物化、队列和 video reuse 已有直接近邻。** SAND/HyCache/Seneca 使“memory-aware materialization”本身站不住；多 epoch 收益可能只是缓存收益。[SAND](https://ina.kaist.ac.kr/assets/bibliography/SAND.pdf)、[HyCache](https://www.usenix.org/system/files/atc25-jha.pdf)
7. **所谓 preprocessing 其实是训练 encoder。** 若收益主要来自 encoder/LLM placement 或 PP bubble，Optimus/DIP/Omni 是必须面对的主近邻，方向应归训练执行。[Optimus](https://www.usenix.org/system/files/atc25-feng.pdf)、[DIP](https://arxiv.org/html/2504.14145v2)
8. **重排收益来自改训练语义。** 跨 update 改样本分布、减少 augmentation、缩帧数或改变 contrastive negatives，均不是严格同语义速度提升。
9. **joint reference 的收益来自先知或更多资源。** 若 reference 知道未来实测时延、享有更多 CPU/远程节点，差距不能归因于 planning；必须拆信息增益和决策增益。
10. **只在刻意少给 worker/内存或单次冷启动成立。** 人工制造 starvation、去掉正常 prefetch 或只报 startup，均应 STOP 或大幅缩小论文范围。

其中“strong residual <5%”目前不是已确认事实，而是必须主动追求的证伪结果。

## 7. Remaining Research Gap

**可证伪的一句话：**

> 在保持每个 optimizer update 的样本、随机变换与损失不变的真实压缩视频训练中，输入表示/执行粒度造成的有界缓冲释放时间差异，是否会使已做双成本负载均衡和资源调优的阶段局部计划，在自然配置下仍稳定比同预算联合计划慢至少 15%？

这是 HYPOTHESIS，不是“已发现的未解决问题”。只有证明重复失败、归因到缺失物理属性、排除简单策略，才可改写成陈述句。

对七项资格的当前状态：真实在线 workload **已支持**；强 baseline 仍失败 **UNKNOWN**；可重复失败 **UNKNOWN**；明确机制 **有候选，未归因验证**；简单 heuristic 无效 **UNKNOWN**；DB-style 求解合理 **条件成立**；有限资源可测 **是，但先固定拓扑**。

## 8. Minimal Falsification Experiment

可直接交给实验同学的配方与执行顺序见 [E0 实验卡](./minimal_experiment.md)。建议从 Qwen2.5-VL-3B 与可完整获取的 LLaVA-Video academic 子集开始；模型、数据和硬件可运行性尚未在本次验证。

### 8.1 先冻结 Legal(P)

定义严格契约 `Legal_strict(P)`：

1. 每次 update 的 `(sample_id, occurrence_id, weight)` 多重集合不变；不丢尾、不重复、不跨 update 搬样本。epoch 的 drop/pad policy 与 mixture 版本固定。
2. 输入 tensors、labels、帧时刻、跨模态对应关系相同；随机变换由冻结的记录或统一的 sample/view/operator-keyed RNG 给出。两边从同一 RNG 契约开始，不能假装兼容任意 worker-local 随机序列。
3. 仅在纯函数独立、数值语义一致时交换算子；crop 与 resize、随机 transform 与 cache、tokenization 与文本过滤不能默认交换。
4. batch regrouping 只在 loss 可按样本/有效 token 可加、位置与 mask 一致时允许。全局 token 归一化固定；梯度累积完才更新参数。
5. BatchNorm、batch-dependent augmentation、MixUp/CutMix、contrastive negatives、stateful decoder/iterator、packing 跨样本 attention 均显式标为屏障或提供等价实现。不能仅比较样本集合。
6. TP 组输入一致、DP 分片完备、PP forward/backward 和 collective 顺序合法；不引入 weight staleness。浮点归约次序允许预声明误差范围，不能声称 bitwise 相同。
7. feature cache key 包含 encoder/tokenizer/transform 版本、参数状态、frame selection、seed/view、dtype。trainable encoder 不能用旧参数特征替代。
8. resume/checkpoint 保留 sampler cursor、mixture version、RNG 和 pending sample 状态；吞吐优化不得改变实际 consumed distribution。

另外定义 `Legal_distributional` 轨道：允许统计等价重排/增强变化，但必须独立验证分布、模型质量和 time-to-quality，不与严格轨道混报。

**近邻 legality 审计：**Pecan 的 relaxed commutativity 属于更弱的质量保持主张；DistTrain 的全局 batch 内重排有明确作用域，但通用梯度等价仍需损失/RNG 条件；DFLOP 的 assignment 约束须结合 trainer 的归一化核验；Minato 的完成即取/慢样本重试不能自动保持批成员和随机 draw；Omni 的数据与 embedding 重分布须保证索引映射与同步。SAND 的协调随机窗口保留 randomness 的作者主张，也不等于原始独立增强轨迹不变。DISTMM 则直接说明 contrastive loss 需要保留跨样本同步。[DISTMM §5](https://www.usenix.org/system/files/nsdi24-huang.pdf)

### 8.2 Workload 与硬件

**第一阶段仅用一台 2–4 GPU Linux 训练机**，CPU 核数与 host RAM 固定并记录；远程分离作为第二配置，使用额外 CPU 节点时必须从同一总预算扣除。没有条件就把 remote placement 标为未测，不通过模拟网络性能补结论。

- 主任务：公开视频-文本 instruction/SFT 数据上的小型 VLM（约 2B–3B、模型官方 recipe 能运行的规模），真实压缩视频、原生长度与分辨率分布；固定 checkpoint、数据 revision 和样本 manifest。具体公开集由可完整获取的视频决定，不能只有 caption/URL 而没有媒体。
- 第一模型保留 trainable vision encoder，或至少独立报告 frozen 情况；避免“缓存全部特征”暗中改变问题。LoRA 可以降低成本，但必须报告它改变 compute/input 比例，不能外推完整 pretraining。
- 第二 workload：有独立访问结构的机器人多视角时间窗或第二视频数据集；图像训练、预处理好特征、text-only 各作阴性对照。一个 corpus 的三个 batch size 不算三个 workload。
- 初期 DP=2/4、PP=1；先回答输入 physical-plan 问题。只有 DP gate 通过才增加 PP=2。没有 PP 实验不得声称降低 PP bubble。

### 8.3 Baseline stack：共同运行时重实现机制，不能拼 artifact

| 层 | 在统一代码/语义中实现的机制 | 必须控制的冲突 |
|---|---|---|
| B0 | tuned workers/threads、persistent workers、pinned memory、正常 prefetch、正确异步 transfer；同时比较成熟视频 decode backend | 不把默认配置当 tuned；记录缓存冷热 |
| B1 | Minato-style tail segregation/超时识别；严格轨道保留原定 batch，只改变任务执行先后 | 若要 ready-first 改批成员，放入 distributional 轨道；重试随机 transform 必须复用 draw |
| B2 | cedar/Pecan-inspired 合法物理备选：CPU/GPU transform、局部/远程切点、fusion/分块、worker parallelism；按输入侧目标选择 | 不是声称移植原 optimizer；CPU/GPU实现必须输出等价，GPU不可用算子不列为可选 |
| B3 | DistTrain-style 分离供数+在同一 update 内按训练成本分配/重排；同预算 | remote 是可选分支；若独立资源可消除 stall，允许它赢 |
| B4 | DFLOP/MegaScale-Data/Omni-inspired 训练感知分组/顺序，按 encoder 与 LLM cost；固定训练拓扑阶段只用对应子机制 | 不同时套多个互相矛盾的 sampler；同一个合法 assignment controller 仲裁 |
| H（强简单反方） | 两维 prep/train LPT/greedy、紧凑 uint8 传输、byte-based queue、demand-driven throttling、profile后小网格/交替优化 | 允许端到端校准和非单调层级；不能故意让强 baseline 缺少易加机制 |
| REF-feasible | 有限合法候选空间内，实测最佳 `(Pd,Pt)` 组合，留出窗口确认 | 同预算、同样本、同计划信息；包含 B0–H 本身 |
| REF-oracle | 用未来已测时延的 trace hindsight 排程，只用于上界筛查 | 不作为可实现方法，不将先知收益当论文收益 |

另加入两个必须允许获胜的分支：① Cosmos 式离线统一 codec/indexed clips（计入存储与预计算摊销）；② rank-synchronous stream selection + 小 look-ahead + token/校准形状时间预算。时间预算可能改变 token mixture，必须保持原定 update 成员与混合权重，或移入 distributional 轨道。

Strong 定义为校准集选定的所有合法 B/H 配置中最优者；不强制 `B0→B1→…` 单调叠加。把多篇论文的 headline speedup 相乘，或把不同版本 artifact 直接连接，都不是 baseline。

### 8.4 最小决策空间与执行顺序

先保持模型拓扑与全局 batch 不变，限制为 8–24 个合法输入备选和 3–6 个训练分组/顺序策略。优先变量是转换位置、uint8/float queue 表示、分块粒度、queue byte cap 与 prep concurrency；不要同时搜索十个维度。

1. **E0-a，语义和零机会筛查。** 保存 256 个自然 update 的样本/增强 manifest。核验输入 hash/数值容差、索引、loss/gradient。用同一 Pt、相同内存预留的预备好输入测 training-ready comparator，量化可去除 input path 的 headroom。它是对照，不把驻留数据偷占/节省的内存算进去。
2. **E0-b，交叉计划测量。** 在校准窗口记录所有备选的 `(r_i, live_bytes, CPU/GPU demand, transfer)`；实跑关键 `(Pd,Pt)` 组合，寻找排名反转。isolated operator profile 只用于提案，不能代替竞争下实际时间。
3. **E0-c，残差归因。** 加入 H 并逐一消融“输入预取不足”“重排不足”“表示过大”“资源竞争”。若一次简单修复就消除差距，记录失败原因并 STOP，而非继续加复杂度。
4. **E0-d，留出验证。** 冻结配置，在不参与选计划的新视频/update 窗口上跑；至少 3 次独立 seed/运行，随机化方案顺序并控制warmup和缓存状态。初始小窗口用于筛查，不用于稳定 p99 宣称；p99 需要更多自然 batch（建议至少数千，最终按置信区间决定）。

不要把 trace 的模拟 makespan 当真实效果。若用 simulator 做 near-exhaustive reference，先以真实运行验证其误差和计划排序；在筛出的最佳几组上测真实 wall time。有限候选的最优值不是全局 oracle，应称 **best-known feasible reference**。

### 8.5 Metrics 与 gate

主指标为真实 step completion time / 完成相同有效 token 的总时间。记录 `batch-ready p50/p95/p99`、exposed training stall、DP rank idle、PP bubble（仅 PP>1）、throughput、peak host/GPU memory、network/IPC bytes、CPU/GPU/copy/decode engine utilization、重规划与warmup成本。

`next(loader)` 等待不等于 exposed GPU stall；只统计无可执行训练工作且其关键依赖为数据的空隙，结合 CPU/GPU timeline 与 ready-input 对照。DP wait 区分 input delay、compute imbalance、collective delay，避免重复计数。

\[
Regret=\frac{T_{strong}-T_{reference}}{T_{reference}}.
\]

| 结果 | 决策 |
|---|---|
| 至少两个独立真实 workload、多组自然资源配置，在留出运行中稳定 ≥15–20%，置信区间支持实质差异；H 无法消除；计划反转可归因且 Legal(P) 通过 | GO：进入限定决策空间的方法设计 |
| 大多数自然配置 <5–10%，或 ready-input headroom 已很小 | STOP 当前候选；可保存测量结果，不强行做 optimizer |
| 只在极少 CPU/内存、人工 mixture 高频震荡、仅冷启动或样本语义改变时成立 | STOP / 明确缩小适用域；不能声称通用多模态训练问题 |
| oracle 有差距，但 feasible reference 没有；或 simple H 同样好 | 停止通用 optimizer 假设；区分预测问题、工程问题或简单策略 |
| 差距仅来自 encoder/LLM pipeline | 转到训练执行近邻审计，不能沿用输入 DAG novelty |

15–20% 与 5–10% 是**本研究的投入决策阈值，不是社区标准**。没有一个精确阈值可以替代效果置信区间、资源成本与适用范围。

### 8.6 最小 trace contract

每行至少保留 `run/step/rank/sample/occurrence/operator/plan` ID、开始结束时间、设备/worker、bytes-in/out、queue bytes、等待原因、decode元数据、所选frame IDs、augmentation key、vision/text tokens、microbatch membership、训练模块开始结束、peak workspace、mixture/model/tokenizer version。跨机器时间需同步或用因果事件校准，避免把时钟偏移当rank tail。

记录预定样本与实际消费样本两个视图；真实视频必须按自然来源分布抽样并固定 manifest。另保存 candidate plan、合法性拒绝原因、配置选择窗口和评估窗口，防止选择偏差。

## 9. Recommended Paper Positioning

当前还不能写成已经有贡献的投稿故事。

- **SIGMOD / PVLDB / ICDE：有条件最匹配。** 若实验证明需要将 release-time、live-byte、共享资源需求作为物理属性，并能以合法变换、属性传播、支配剪枝或小而明确的 cost-based search 解决多种输入计划的反转，DB 定位自然。若最终只是两种 loader 的开关，不足以支持通用物理优化叙事。
- **MLSys：若主要贡献是可复现 workload characterization、训练语义与低成本调度，可能更直接。** 必须展示模型/数据/硬件条件与端到端质量，不仅 loader microbenchmark。
- **OSDI / EuroSys / ASPLOS：若核心是跨层运行时或资源共执行，且有充分系统机制与规模证据。** GPU资源竞争最接近 RAP/FusionFlow；模型 bubble 最接近 Optimus/DIP/Omni。仅有小规模组合 optimizer，不应先选大系统 venue 再补故事。

建议的论文问题标题方向仅在 E0 成立后使用：**Memory-Bounded Input Plan Selection for Synchronous Video Training**。重点是可复现的全局错误选择及其修复，不是“统一所有因素”。

## 10. Evidence Ledger

完整的逐工作 ledger、检查过的代码路径与版本、未能核验的部分见 [Evidence Ledger](./evidence_ledger.md)。逐 cell 所依赖的 locator 见 [Coverage Matrix](./coverage_matrix.md)。

### 审计约束

- 论文正文/附录的结论与源码快照可能不同，分别记录；尤其不能由 artifact 文件名推断实现完整。
- paper-only production evaluation 是作者提供的生产证据，不是本次取得的原始 production trace。
- 预印本和会议版本分别标记；DFLOP 的 SIGMOD26 与 Mixtera 的 SIGMOD26 已由官方会议 program 核验，不能只沿用旧 arXiv venue 标签。
- 未公开、无法下载或本次未完成源码审计的地方保留 UNKNOWN；此次没有得到“所有系统都不支持”的全称证明。
- 本报告借用了既有研究记录中的反证优先、真实 workload 与 measurement-only gate 方法；本方向的文献事实均重新检索，不将旧方向结论当作当前证据。

**可执行的下一步只有一个：冻结一个真实视频训练 recipe 和 Legal(P)，先测 B4/H 与 ready-input/有限 joint reference 的残差。没有这个结果，暂不开发通用 joint optimizer。**
