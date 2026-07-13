# Neural Architecture Figures

This document mimics marker-pdf output where extracted figures are emitted as reference-style images with caption text in the body.

## Transformer Block Diagram

The original Transformer architecture consists of an encoder stack and a decoder stack connected via cross-attention.

![Figure 1][img_ref_0]

*Figure 1: The Transformer — model architecture showing encoder (left) and decoder (right) stacks with multi-head attention and feed-forward sub-layers.*

Each encoder layer applies self-attention followed by a position-wise feed-forward network. Residual connections and layer normalization wrap both sub-layers.

## Attention Mechanism

Scaled dot-product attention computes compatibility between queries and keys before weighting values.

![Figure 2][img_ref_1]

*Figure 2: Scaled Dot-Product Attention — queries, keys, and values are projected; attention weights are computed via softmax of scaled dot products.*

Multi-head attention runs several attention operations in parallel and concatenates the results.

## Training Curves

Learning curves for the base model on the WMT 2014 English-to-German translation task.

![Figure 3][img_ref_2]

*Figure 3: Training and validation loss over 100k steps. The base model converges after approximately 12 hours on 8 P100 GPUs.*

## Inline Reference Without Caption

Sometimes marker-pdf emits a bare image reference mid-paragraph, as in: the model diagram ![Figure 1][img_ref_0] illustrates the overall data flow from source tokens to target tokens.

## Figure References in Text

As shown in Figure 1 and Figure 2, attention replaces recurrence entirely. Figure 3 confirms stable convergence without learning-rate warmup beyond the first 4,000 steps.

[img_ref_0]: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==

[img_ref_1]: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==

[img_ref_2]: data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==