# coding: utf-8 -*-
#
# This file is part of SIDEKIT.
#
# SIDEKIT is a python package for speaker verification.
# Home page: http://www-lium.univ-lemans.fr/sidekit/
#
# SIDEKIT is a python package for speaker verification.
# Home page: http://www-lium.univ-lemans.fr/sidekit/
#
# SIDEKIT is free software: you can redistribute it and/or modify
# it under the terms of the GNU LLesser General Public License as
# published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
#
# SIDEKIT is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with SIDEKIT.  If not, see <http://www.gnu.org/licenses/>.

"""
Copyright 2014-2021 Anthony Larcher, Yevhenii Prokopalo
"""


import os
import torch


os.environ['MKL_THREADING_LAYER'] = 'GNU'

__license__ = "LGPL"
__author__ = "Anthony Larcher"
__copyright__ = "Copyright 2015-2021 Anthony Larcher"
__maintainer__ = "Anthony Larcher"
__email__ = "anthony.larcher@univ-lemans.fr"
__status__ = "Production"
__docformat__ = 'reS'


class MeanStdPooling(torch.nn.Module):
    """
    Mean and Standard deviation pooling
    """
    def __init__(self):
        """

        """
        super(MeanStdPooling, self).__init__()
        pass

    def forward(self, x):
        """

        :param x: [B, C*F, T]
        :return:
        """
        if len(x.shape) == 4:
            # [B, C, F, T]
            x = x.permute(0, 1, 3, 2)
            x = x.flatten(start_dim=1, end_dim=2)
        # [B, C*F]
        mean = torch.mean(x, dim=2)
        # [B, C*F]
        std = torch.std(x, dim=2)
        # [B, 2*C*F]
        return torch.cat([mean, std], dim=1)


class ChannelWiseCorrPooling(torch.nn.Module):
    """

    """
    def __init__(self, in_channels=256, out_channels=64, in_freqs=10, channels_dropout=0.25):
        super(ChannelWiseCorrPooling, self).__init__()
        self.channels_dropout = channels_dropout
        self.merge_freqs_count = 2
        assert in_freqs % self.merge_freqs_count == 0
        self.groups = in_freqs//self.merge_freqs_count
        self.out_channels = out_channels
        self.out_dim = int(self.out_channels*(self.out_channels-1)/2)*self.groups
        self.L_proj = torch.nn.Conv2d(in_channels*self.groups, out_channels*self.groups, kernel_size=(1, 1), groups=self.groups)
        # self.L_proj = torch.nn.Conv2d(in_channels, out_channels, kernel_size=(1, 1))
        self.mask = torch.tril(torch.ones((out_channels, out_channels)), diagonal=-1).type(torch.BoolTensor)

    def forward(self, x):
        """

        :param x: [B, C, T, F]
        :return:
        """
        batch_size=x.shape[0]
        num_locations = x.shape[-1]*x.shape[-2]/self.groups
        self.mask = self.mask.to(x.device)
        if self.training:
            x *= torch.nn.functional.dropout(torch.ones((1, x.shape[1], 1, 1), device=x.device), p=self.channels_dropout)
        # [B, T, C, F]
        x = x.permute(0, 2, 1, 3)
        # [B, T, C, Fr, f]
        x = x.reshape(x.shape[0], x.shape[1], x.shape[-2], self.groups, self.merge_freqs_count)
        # [B, T, f, Fr, C]
        x = x.permute(0, 1, 4, 3, 2)
        # [B, T, f, Fr*C]
        x = x.flatten(start_dim=3, end_dim=4)
        # [B, Fr*C, T, f]
        x = x.permute(0, 3, 1, 2)
        # [B, Fr*C', T, f]
        x = self.L_proj(x)
        # [B, Fr, C', Tr]
        x = x.reshape(x.shape[0], self.groups, self.out_channels, -1)
        x -= torch.mean(x, axis=-1, keepdims=True)
        out = x/(torch.std(x, axis=-1, keepdims=True) + 1e-5)
        # [B, C', C']
        out = torch.einsum('abci,abdi->abcd', out, out)
        # [B, C'*(C'-1)/2]
        out = torch.masked_select(out, self.mask).reshape(batch_size, -1)
        out = out / num_locations
        return out

class AttentivePooling(torch.nn.Module):
    """
    Mean and Standard deviation attentive pooling
    """
    def __init__(self, num_channels, num_freqs=10, attention_channels=128, global_context=False):
        """

        """
        # TODO Make global_context configurable (True/False)
        # TODO Make convolution parameters configurable
        super(AttentivePooling, self).__init__()
        in_factor = 3 if global_context else 1
        self.attention = torch.nn.Sequential(
            torch.nn.Conv1d(num_channels * num_freqs * in_factor, attention_channels, kernel_size=1),
            torch.nn.ReLU(),
            torch.nn.BatchNorm1d(attention_channels),
            torch.nn.Tanh(),
            torch.nn.Conv1d(attention_channels, num_channels * num_freqs, kernel_size=1),
            torch.nn.Softmax(dim=2),
        )
        self.global_context = global_context
        self.gc = MeanStdPooling()

    def new_parameter(self, *size):
        out = torch.nn.Parameter(torch.FloatTensor(*size))
        torch.nn.init.xavier_normal_(out)
        return out

    def forward(self, x):
        """

        :param x: [B, C*F, T]
        :return:
        """
        if len(x.shape) == 4:
            # [B, C, F, T]
            x = x.permute(0, 1, 3, 2)
            # [B, C*F, T]
            x = x.flatten(start_dim=1, end_dim=2)
        if self.global_context:
            w = self.attention(torch.cat([x, self.gc(x).unsqueeze(2).repeat(1, 1, x.shape[-1])], dim=1))
        else:
            w = self.attention(x)

        mu = torch.sum(x * w, dim=2)
        rh = torch.sqrt( ( torch.sum((x**2) * w, dim=2) - mu**2 ).clamp(min=1e-9) )
        x = torch.cat((mu, rh),1)
        x = x.view(x.size()[0], -1)
        return x


class AttentiveStatsPool(torch.nn.Module):
    """
    Attentive weighted mean and standard deviation pooling.
    """
    def __init__(self, in_dim, attention_channels=128, global_context_att=False):
        super().__init__()
        self.global_context_att = global_context_att

        # Use Conv1d with stride == 1 rather than Linear, then we don't need to transpose inputs.
        if global_context_att:
            self.linear1 = nn.Conv1d(in_dim * 3, attention_channels, kernel_size=1)  # equals W and b in the paper
        else:
            self.linear1 = nn.Conv1d(in_dim, attention_channels, kernel_size=1)  # equals W and b in the paper
        self.linear2 = nn.Conv1d(attention_channels, in_dim, kernel_size=1)  # equals V and k in the paper

    def forward(self, x):
        """

        :param x:
        :return:
        """
        if self.global_context_att:
            context_mean = torch.mean(x, dim=-1, keepdim=True).expand_as(x)
            context_std = torch.sqrt(torch.var(x, dim=-1, keepdim=True) + 1e-10).expand_as(x)
            x_in = torch.cat((x, context_mean, context_std), dim=1)
        else:
            x_in = x

        # DON'T use ReLU here! In experiments, I find ReLU hard to converge.
        alpha = torch.tanh(self.linear1(x_in))
        # alpha = F.relu(self.linear1(x_in))
        alpha = torch.softmax(self.linear2(alpha), dim=2)
        mean = torch.sum(alpha * x, dim=2)
        residuals = torch.sum(alpha * (x ** 2), dim=2) - mean ** 2
        std = torch.sqrt(residuals.clamp(min=1e-9))
        return torch.cat([mean, std], dim=1)


class GruPooling(torch.nn.Module):
    """
    Pooling done by using a recurrent network
    """
    def __init__(self, input_size, gru_node, nb_gru_layer):
        """

        :param input_size:
        :param gru_node:
        :param nb_gru_layer:
        """
        super(GruPooling, self).__init__()
        self.lrelu_keras = torch.nn.LeakyReLU(negative_slope = 0.3)
        self.bn_before_gru = torch.nn.BatchNorm1d(num_features = input_size)
        self.gru = torch.nn.GRU(input_size = input_size,
                                hidden_size = gru_node,
                                num_layers = nb_gru_layer,
                                batch_first = True)

    def forward(self, x):
        """

        :param x:
        :return:
        """
        x = self.bn_before_gru(x)
        x = self.lrelu_keras(x)
        x = x.permute(0, 2, 1)  # (batch, filt, time) >> (batch, time, filt)
        self.gru.flatten_parameters()
        x, _ = self.gru(x)
        x = x[:, -1, :]

        return x
