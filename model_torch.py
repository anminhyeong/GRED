import torch
import torch.nn as nn
import torch.functional as F
from LRU_pytorch import LRU

class MLP(nn.Module):
    def __init__(self, dim_h, expand=1, drop_rate=0.):
        super(MLP, self).__init__()
        self.dim_h = dim_h
        self.expand = expand
        self.drop_rate = drop_rate

        self.layer_norm = nn.LayerNorm(dim_h)
        self.dense1 = nn.Linear(dim_h, expand * dim_h)
        self.dense2 = nn.Linear(expand * dim_h, dim_h)
        self.dropout = nn.Dropout(drop_rate)

    def forward(self, inputs, training=False):
        x = self.layer_norm(inputs)
        x = self.dense1(x)
        x = F.gelu(x)
        x = self.dropout(x) if training else x
        x = self.dense2(x)
        x = self.dropout(x) if training else x
        return x + inputs

class GRED(nn.Module):
    def __init__(self, dim_v, dim_h, expand=1, r_min=0., r_max=1., max_phase=6.28, drop_rate=0., act="full-glu"):
        super(GRED, self).__init__()
        self.dim_v = dim_v
        self.dim_h = dim_h
        self.expand = expand
        self.r_min = r_min
        self.r_max = r_max
        self.max_phase = max_phase
        self.drop_rate = drop_rate
        self.act = act

        self.mlp = MLP(dim_h, expand, drop_rate)
        self.lru = LRU(dim_v, dim_h, r_min, r_max, max_phase)

    def forward(self, inputs, dist_masks, training=False):
        # Swap axes and perform matrix multiplication
        xs = torch.einsum('bij,bjk->bik', dist_masks.transpose(0, 1), inputs)
        xs = self.mlp(xs, training=training)
        x = self.lru(xs, training=training)
        return x
    
class SuperPixel(nn.Module):
    def __init__(self, num_layers, dim_o, dim_v, dim_h, expand=1, r_min=0., r_max=1.,
                 max_phase=6.28, drop_rate=0., act="full-glu"):
        super(SuperPixel, self).__init__()
        self.num_layers = num_layers
        self.dim_o = dim_o
        self.dim_v = dim_v
        self.dim_h = dim_h
        self.expand = expand
        self.r_min = r_min
        self.r_max = r_max
        self.max_phase = max_phase
        self.drop_rate = drop_rate
        self.act = act

        # Define initial dense layers
        self.initial_dense1 = nn.Linear(dim_h, dim_h)
        self.initial_dense2 = nn.Linear(dim_h, dim_h)

        # Define GRED layers
        self.gred_layers = nn.ModuleList([
            GRED(dim_v, dim_h, expand, r_min, r_max, max_phase, drop_rate, act)
            for _ in range(num_layers)
        ])

        # Final dense layer
        self.final_dense = nn.Linear(dim_h, dim_o)

    def forward(self, inputs, node_masks, dist_masks, training=False):
        # Initial dense layers with GELU activation
        x = self.initial_dense1(inputs)
        x = F.gelu(x)
        x = self.initial_dense2(x)

        # Apply GRED layers
        for gred_layer in self.gred_layers:
            x = gred_layer(x, dist_masks, training=training)

        # Apply node masks: zero out where mask is False
        node_masks_expanded = node_masks.unsqueeze(-1)  # Expand dimensions for broadcasting
        x = torch.where(node_masks_expanded, x, torch.zeros_like(x))

        # Sum over nodes and normalize by the mask sum
        mask_sum = torch.sum(node_masks, dim=1, keepdim=True)
        x = torch.sum(x, dim=1) / mask_sum

        # Final dense layer
        x = self.final_dense(x)
        return x