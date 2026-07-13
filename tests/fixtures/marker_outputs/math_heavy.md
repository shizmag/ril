# Mathematical Notation in Academic PDFs

This fixture exercises multiple LaTeX delimiter styles commonly produced by marker-pdf when converting scientific documents.

## Inline and Display Math

Einstein's mass-energy equivalence can be written inline as $E = mc^2$, or in display form:

$$E = mc^2$$

The quadratic formula $x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$ solves equations of the form $ax^2 + bx + c = 0$.

For a simple power expression, we write $x^2 + y^2 = r^2$.

## Alternative Delimiters

Some PDF extractors emit LaTeX with backslash-paren delimiters for inline math, such as \( \nabla \cdot \mathbf{E} = \frac{\rho}{\epsilon_0} \).

Display equations may appear with bracket delimiters:

\[
\int_{-\infty}^{\infty} e^{-x^2}\, dx = \sqrt{\pi}
\]

The softmax function over logits $z_i$ is often written as \( \sigma(z_i) = \frac{e^{z_i}}{\sum_j e^{z_j}} \).

## Mixed Delimiters in One Paragraph

Consider the heat equation \( \frac{\partial u}{\partial t} = \alpha \nabla^2 u \) alongside its steady-state form:

\[
\nabla^2 u = 0
\]

We also retain dollar-delimited display math for matrix notation:

$$
\mathbf{A} = \begin{bmatrix}
1 & 0 & 0 \\
0 & 1 & 0 \\
0 & 0 & 1
\end{bmatrix}
$$

## Code Blocks Must Not Be Math

The following fenced code block contains dollar signs that must remain literal text, not math delimiters:

```python
# Shell variable expansion — not LaTeX
price = 100
tax_rate = 0.08
total = f"${price * (1 + tax_rate):.2f}"
print(f"Total: ${total}")
```

Similarly, inline code like `cost = $19.99` and `regex = r'\$[0-9]+'` should not be interpreted as mathematics.

```bash
# Another example with dollar signs
export BUDGET="$5000"
echo "Budget is $BUDGET"
```

## Probability and Statistics

The Gaussian PDF with mean $\mu$ and variance $\sigma^2$ is:

$$
p(x \mid \mu, \sigma^2) = \frac{1}{\sqrt{2\pi\sigma^2}} \exp\left(-\frac{(x-\mu)^2}{2\sigma^2}\right)
$$

Bayes' rule in inline form: \( P(A \mid B) = \frac{P(B \mid A)\, P(A)}{P(B)} \).

## Summations and Products

The definition of $e$ uses a series:

\[
e = \sum_{n=0}^{\infty} \frac{1}{n!}
\]

And the factorial can be expressed as a product: \( n! = \prod_{k=1}^{n} k \).