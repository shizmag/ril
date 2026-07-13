# Experimental Results and Structured Content

This fixture provides markdown tables and lists as commonly extracted from academic PDFs by marker-pdf.

## Benchmark Results

Table 1 summarizes BLEU scores on the WMT 2014 English-to-German translation task.

| Model | BLEU | Training Cost (FLOPs) | Params |
|-------|------|----------------------|--------|
| ByteNet | 23.75 | — | — |
| ConvS2S | 25.16 | 9.6 × 10^18 | — |
| GNMT + RL | 24.6 | 2.3 × 10^19 | 213M |
| Transformer (base) | 27.3 | 3.3 × 10^18 | 65M |
| Transformer (big) | 28.4 | 2.3 × 10^19 | 213M |

*Table 1: Machine translation results on WMT 2014 English-to-German. Training costs are estimates based on hardware used in our experiments.*

## Ablation Study

| Configuration | BLEU | Δ vs Base |
|---------------|------|-----------|
| Base model | 27.3 | — |
| No positional encoding | 25.5 | −1.8 |
| Single attention head | 26.1 | −1.2 |
| Reduced $d_{\text{model}}$ | 25.9 | −1.4 |

## Unordered Lists

Key components of the Transformer encoder layer:

- Multi-head self-attention sub-layer
- Position-wise feed-forward network
- Residual connection around each sub-layer
- Layer normalization after each sub-layer

Training hyperparameters used in our experiments:

- Batch size: 25,000 source and target tokens
- Optimizer: Adam with $\beta_1 = 0.9$, $\beta_2 = 0.98$, $\epsilon = 10^{-9}$
- Learning rate schedule: warmup for 4,000 steps, then decay proportionally to inverse square root of step number
- Dropout rate: 0.1
- Label smoothing: $\epsilon_{ls} = 0.1$

## Ordered Lists

The training procedure proceeds as follows:

1. Tokenize source and target sentences with a shared 37,000-token vocabulary.
2. Pad batches to the maximum sequence length within each batch.
3. Apply dropout to attention weights and residual connections.
4. Compute cross-entropy loss with label smoothing.
5. Backpropagate through all encoder and decoder layers.
6. Evaluate on newstest2014 every 1,000 steps.

## Nested Lists

Datasets used for evaluation:

- Translation
  - WMT 2014 English-to-German (4.5M sentence pairs)
  - WMT 2014 English-to-French (36M sentence pairs)
- Parsing
  - Penn Treebank WSJ (40K training sentences)
  - Semi-supervised setting with 17M sentences from CoNLL 2012

## Mixed Content

The base Transformer achieves state-of-the-art results while requiring:

1. Less training time than recurrent architectures
2. Fewer parameters than the GNMT ensemble
3. Full parallelization across sequence positions

Hardware configuration:

- 8 NVIDIA P100 GPUs
- 12 hours training for the base model
- 3.5 days training for the big model