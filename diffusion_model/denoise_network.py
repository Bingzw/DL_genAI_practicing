import torch
import torch.nn as nn


def sinusoidal_embedding(n, d):
    """
    calculate the position embedding
    :param n: number of time steps
    :param d: time embedding dimension
    :return: time step embeddings
    """
    embedding = torch.zeros(n, d)
    omega_k = torch.tensor([1 / 10000 ** (2 * j / d) for j in range(d)])
    omega_k = omega_k.reshape((1, d))
    t = torch.arange(n).reshape((n, 1))
    embedding[:, ::2] = torch.sin(t * omega_k[:, ::2])
    embedding[:, 1::2] = torch.cos(t * omega_k[:, 1::2])

    return embedding


class MLPBlock(nn.Module):
    def __init__(self, shape, in_c, out_c, kernel_size=3, stride=1, padding=1, activation=None, normalize=True):
        super().__init__()
        self.ln = nn.LayerNorm(shape)
        self.conv1 = nn.Conv2d(in_c, out_c, kernel_size, stride, padding)
        self.conv2 = nn.Conv2d(out_c, out_c, kernel_size, stride, padding)
        self.activation = nn.SiLU() if activation is None else activation
        self.normalize = normalize

    def forward(self, x):
        out = self.ln(x) if self.normalize else x
        out = self.conv1(out)
        out = self.activation(out)
        out = self.conv2(out)
        out = self.activation(out)
        return out


class UNet(nn.Module):
    def __init__(self, n_steps=1000, time_emb_dim=100):
        super().__init__()
        # time step embedding
        self.n_steps = n_steps
        self.time_emb = nn.Embedding(n_steps, time_emb_dim)
        self.time_emb.weight.data = sinusoidal_embedding(n_steps, time_emb_dim)
        self.time_emb.requires_grad = False
        # First Half
        self.te1 = self._make_te(time_emb_dim, 1)  # te is short for time embedding
        self.b1 = nn.Sequential(
            MLPBlock((1, 28, 28), 1, 10),
            MLPBlock((10, 28, 28), 10, 10),
            MLPBlock((10, 28, 28), 10, 10)
        )
        self.down1 = nn.Conv2d(10, 10, 4, 2, 1)

        self.te2 = self._make_te(time_emb_dim, 1)
        self.b2 = nn.Sequential(
            MLPBlock((10, 14, 14), 10, 20),
            MLPBlock((20, 14, 14), 20, 20),
            MLPBlock((20, 14, 14), 20, 20)
        )
        self.down2 = nn.Conv2d(20, 20, 4, 2, 1)

        self.te3 = self._make_te(time_emb_dim, 20)
        self.b3 = nn.Sequential(
            MLPBlock((20, 7, 7), 20, 40),
            MLPBlock((40, 7, 7), 40, 40),
            MLPBlock((40, 7, 7), 40, 40)
        )
        self.down3 = nn.Sequential(
            nn.Conv2d(40, 40, 2, 1),
            nn.SiLU(),
            nn.Conv2d(40, 40, 4, 2, 1)
        )

        # Bottleneck
        self.te_mid = self._make_te(time_emb_dim, 40)
        self.b_mid = nn.Sequential(
            MLPBlock((40, 3, 3), 40, 20),
            MLPBlock((20, 3, 3), 20, 20),
            MLPBlock((20, 3, 3), 20, 40)
        )

        # Second Half
        self.up1 = nn.Sequential(
            nn.ConvTranspose2d(40, 40, 4, 2, 1),
            nn.SiLU(),
            nn.ConvTranspose2d(40, 40, 2, 1)
        )

        self.te4 = self._make_te(time_emb_dim, 80)
        self.b4 = nn.Sequential(
            MLPBlock((80, 7, 7), 80, 40),
            MLPBlock((40, 7, 7), 40, 20),
            MLPBlock((20, 7, 7), 20, 20)
        )

        self.up2 = nn.ConvTranspose2d(20, 20, 4, 2, 1)
        self.te5 = self._make_te(time_emb_dim, 40)
        self.b5 = nn.Sequential(
            MLPBlock((40, 14, 14), 40, 20),
            MLPBlock((20, 14, 14), 20, 10),
            MLPBlock((10, 14, 14), 10, 10)
        )

        self.up3 = nn.ConvTranspose2d(10, 10, 4, 2, 1)
        self.te_out = self._make_te(time_emb_dim, 20)
        self.b_out = nn.Sequential(
            MLPBlock((20, 28, 28), 20, 10),
            MLPBlock((10, 28, 28), 10, 10),
            MLPBlock((10, 28, 28), 10, 10, normalize=False)
        )

        self.conv_out = nn.Conv2d(10, 1, 3, 1, 1)

    def forward(self, x, t):
        t = self.time_emb(t)
        n = len(x)
        out1 = self.b1(x + self.te1(t).reshape(n, -1, 1, 1))  # (N, 10, 28, 28)
        out2 = self.b2(self.down1(out1) + self.te2(t).reshape(n, -1, 1, 1))  # (N, 20, 14, 14)
        out3 = self.b3(self.down2(out2) + self.te3(t).reshape(n, -1, 1, 1))  # (N, 40, 7, 7)
        out_mid = self.b_mid(self.down3(out3) + self.te_mid(t).reshape(n, -1, 1, 1))  # (N, 40, 3, 3)
        out4 = torch.cat((out3, self.up1(out_mid)), dim=1)  # (N, 80, 7, 7)
        out4 = self.b4(out4 + self.te4(t).reshape(n, -1, 1, 1))  # (N, 20, 7, 7)
        out5 = torch.cat((out2, self.up2(out4)), dim=1)  # (N, 40, 14, 14)
        out5 = self.b5(out5 + self.te5(t).reshape(n, -1, 1, 1))  # (N, 10, 14, 14)
        out = torch.cat((out1, self.up3(out5)), dim=1)  # (N, 20, 28, 28)
        out = self.b_out(out + self.te_out(t).reshape(n, -1, 1, 1))  # (N, 1, 28, 28)
        out = self.conv_out(out)

        return out

    def _make_te(self, dim_in, dim_out):
        return nn.Sequential(
            nn.Linear(dim_in, dim_out),
            nn.SiLU(),
            nn.Linear(dim_out, dim_out)
        )