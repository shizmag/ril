# Attention Is All You Need

Ashish Vaswani<sup>1</sup>, Noam Shazeer<sup>1</sup>, Niki Parmar<sup>1</sup>

<sup>1</sup>Google Brain

## Abstract

The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms, dispensing with recurrence and convolutions entirely. Experiments on two machine translation tasks show these models to be superior in quality while being more parallelizable and requiring significantly less time to train.

# 1 Introduction

Recurrent neural networks, long short-term memory [\(Hochreiter and Schmidhuber, 1997\)](#page-0-0) and gated recurrent [\(Cho et al., 2014\)](#page-0-1) neural networks in particular, have been firmly established as state of the art approaches in sequence modeling and transduction problems such as language modeling and machine translation [\(Sutskever et al., 2014\)](#page-0-2).

Recurrent models typically factor computation along the symbol positions of the input and output sequences. Aligning the positions to steps in computation time, they generate a sequence of hidden states $h_t$, as a function of the previous hidden state $h_{t-1}$ and the input for position $t$. This inherently sequential nature precludes parallelization within training examples, which becomes critical at longer sequence lengths, as memory constraints limit batching across examples.

## 1.1 Motivation

Attention mechanisms have become an integral part of compelling sequence modeling and transduction models in various tasks, allowing modeling of dependencies without regard to their distance in the input or output sequences [\(Bahdanau et al., 2015\)](#page-0-3). However, in most cases such attention mechanisms are used in conjunction with a recurrent network.

In this work we propose the Transformer, a model architecture eschewing recurrence and instead relying entirely on an attention mechanism to draw global dependencies between input and output. The Transformer allows for significantly more parallelization and can reach a new state of the art in translation quality after being trained for as little as twelve hours on eight P100 GPUs.

## 1.2 Contributions

Our main contributions are the following:

- We propose a new simple network architecture based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.
- We show that the Transformer generalizes well to other tasks by applying it successfully to English constituency parsing.
- We make the code available at https://github.com/tensorflow/tensor2tensor.

# 2 Background

The goal of reducing sequential computation also forms the foundation of the Extended Neural GPU [\(Kaiser and Bengio, 2016\)](#page-0-4), ByteNet [\(Kalchbrenner et al., 2016\)](#page-0-5) and ConvS2S [\(Gehring et al., 2017\)](#page-0-6), all of which use convolutional neural networks as basic building block, computing hidden representations in parallel for all input and output positions.

Self-attention, sometimes called intra-attention, is an attention mechanism relating different positions of a single sequence in order to compute a representation of the sequence. Self-attention has been used successfully in a variety of tasks including reading comprehension, abstractive summarization, textual entailment and learning task-independent sentence representations [\(Cheng et al., 2016\)](#page-0-7).

## 2.1 Scaled Dot-Product Attention

An attention function can be described as mapping a query and a set of key-value pairs to an output, where the query, keys, values, and output are all vectors. The output is computed as a weighted sum of the values, where the weight assigned to each value is computed by a compatibility function of the query with the corresponding key.

We call our particular attention "Scaled Dot-Product Attention". The input consists of queries and keys of dimension $d_k$, and values of dimension $d_v$. We compute the dot products of the query with all keys, divide each by $\sqrt{d_k}$, and apply a softmax function to obtain the weights on the values.

# 3 Model Architecture

Most competitive neural sequence transduction models have an encoder-decoder structure [\(Cho et al., 2014;](#page-0-1) [Sutskever et al., 2014\)](#page-0-2). Here, the encoder maps an input sequence of symbol representations $(x_1, \ldots, x_n)$ to a sequence of continuous representations $\mathbf{z} = (z_1, \ldots, z_n)$. Given $\mathbf{z}$, the decoder then generates an output sequence $(y_1, \ldots, y_m)$ of symbols one element at a time.

The Transformer follows this overall architecture using stacked self-attention and point-wise, fully connected layers for both the encoder and decoder, shown in the left and right halves of Figure 1, respectively.

## 3.1 Encoder and Decoder Stacks

**Encoder:** The encoder is composed of a stack of $N=6$ identical layers. Each layer has two sub-layers. The first is a multi-head self-attention mechanism, and the second is a simple, position-wise fully connected feed-forward network. We employ a residual connection [\(He et al., 2016\)](#page-0-8) around each of the two sub-layers, followed by layer normalization [\(Ba et al., 2016\)](#page-0-9).

**Decoder:** The decoder is also composed of a stack of $N=6$ identical layers. In addition to the two sub-layers in each encoder layer, the decoder inserts a third sub-layer, which performs multi-head attention over the output of the encoder stack.

## 3.2 Positional Encoding

Since our model contains no recurrence and no convolution, in order for the model to make use of the order of the sequence, we must inject some information about the relative or absolute position of the tokens in the sequence. To this end, we add "positional encodings" to the input embeddings at the bottoms of the encoder and decoder stacks.

We use sine and cosine functions of different frequencies:

$$PE_{(pos, 2i)} = \sin\left(\frac{pos}{10000^{2i/d_{\text{model}}}}\right)$$

$$PE_{(pos, 2i+1)} = \cos\left(\frac{pos}{10000^{2i/d_{\text{model}}}}\right)$$

where $pos$ is the position and $i$ is the dimension. That is, each dimension of the positional encoding corresponds to a sinusoid.