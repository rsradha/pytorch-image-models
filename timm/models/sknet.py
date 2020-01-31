import math

from torch import nn as nn

from timm.models.registry import register_model
from timm.models.helpers import load_pretrained
from timm.models.conv2d_layers import SelectiveKernelConv, ConvBnAct
from timm.models.resnet import ResNet, SEModule
from timm.data import IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD


def _cfg(url='', **kwargs):
    return {
        'url': url,
        'num_classes': 1000, 'input_size': (3, 224, 224), 'pool_size': (7, 7),
        'crop_pct': 0.875, 'interpolation': 'bilinear',
        'mean': IMAGENET_DEFAULT_MEAN, 'std': IMAGENET_DEFAULT_STD,
        'first_conv': 'conv1', 'classifier': 'fc',
        **kwargs
    }


default_cfgs = {
    'skresnet18': _cfg(url=''),
    'skresnet26d': _cfg()
}


class SelectiveKernelBasic(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None, cardinality=1, base_width=64,
                 use_se=False, sk_kwargs=None, reduce_first=1, dilation=1, first_dilation=None,
                 drop_block=None, drop_path=None, act_layer=nn.ReLU, norm_layer=nn.BatchNorm2d):
        super(SelectiveKernelBasic, self).__init__()

        sk_kwargs = sk_kwargs or {}
        conv_kwargs = dict(drop_block=drop_block, act_layer=act_layer, norm_layer=norm_layer)
        assert cardinality == 1, 'BasicBlock only supports cardinality of 1'
        assert base_width == 64, 'BasicBlock doest not support changing base width'
        first_planes = planes // reduce_first
        out_planes = planes * self.expansion
        first_dilation = first_dilation or dilation

        _selective_first = True  # FIXME temporary, for experiments
        if _selective_first:
            self.conv1 = SelectiveKernelConv(
                inplanes, first_planes, stride=stride, dilation=first_dilation, **conv_kwargs, **sk_kwargs)
            conv_kwargs['act_layer'] = None
            self.conv2 = ConvBnAct(
                first_planes, out_planes, kernel_size=3, dilation=dilation, **conv_kwargs)
        else:
            self.conv1 = ConvBnAct(
                inplanes, first_planes, kernel_size=3, stride=stride, dilation=first_dilation, **conv_kwargs)
            conv_kwargs['act_layer'] = None
            self.conv2 = SelectiveKernelConv(
                first_planes, out_planes, dilation=dilation, **conv_kwargs, **sk_kwargs)
        self.se = SEModule(out_planes, planes // 4) if use_se else None
        self.act = act_layer(inplace=True)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation
        self.drop_block = drop_block
        self.drop_path = drop_path

    def zero_init_last_bn(self):
        nn.init.zeros_(self.conv2.bn.weight)

    def forward(self, x):
        residual = x
        x = self.conv1(x)
        x = self.conv2(x)
        if self.se is not None:
            x = self.se(x)
        if self.drop_path is not None:
            x = self.drop_path(x)
        if self.downsample is not None:
            residual = self.downsample(residual)
        x += residual
        x = self.act(x)
        return x


class SelectiveKernelBottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None,
                 cardinality=1, base_width=64, use_se=False, sk_kwargs=None,
                 reduce_first=1, dilation=1, first_dilation=None,
                 drop_block=None, drop_path=None,
                 act_layer=nn.ReLU, norm_layer=nn.BatchNorm2d):
        super(SelectiveKernelBottleneck, self).__init__()

        sk_kwargs = sk_kwargs or {}
        conv_kwargs = dict(drop_block=drop_block, act_layer=act_layer, norm_layer=norm_layer)
        width = int(math.floor(planes * (base_width / 64)) * cardinality)
        first_planes = width // reduce_first
        out_planes = planes * self.expansion
        first_dilation = first_dilation or dilation

        self.conv1 = ConvBnAct(inplanes, first_planes, kernel_size=1, **conv_kwargs)
        self.conv2 = SelectiveKernelConv(
            first_planes, width, stride=stride, dilation=first_dilation, groups=cardinality,
            **conv_kwargs, **sk_kwargs)
        conv_kwargs['act_layer'] = None
        self.conv3 = ConvBnAct(width, out_planes, kernel_size=1, **conv_kwargs)
        self.se = SEModule(out_planes, planes // 4) if use_se else None
        self.act = act_layer(inplace=True)
        self.downsample = downsample
        self.stride = stride
        self.dilation = dilation
        self.drop_block = drop_block
        self.drop_path = drop_path

    def zero_init_last_bn(self):
        nn.init.zeros_(self.conv3.bn.weight)

    def forward(self, x):
        residual = x
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        if self.se is not None:
            x = self.se(x)
        if self.drop_path is not None:
            x = self.drop_path(x)
        if self.downsample is not None:
            residual = self.downsample(residual)
        x += residual
        x = self.act(x)
        return x


@register_model
def skresnet26d(pretrained=False, num_classes=1000, in_chans=3, **kwargs):
    """Constructs a ResNet-26 model.
    """
    default_cfg = default_cfgs['skresnet26d']
    sk_kwargs = dict(
        keep_3x3=False,
    )
    model = ResNet(
        SelectiveKernelBottleneck, [2, 2, 2, 2],  stem_width=32, stem_type='deep', avg_down=True,
        num_classes=num_classes, in_chans=in_chans, block_args=dict(sk_kwargs=sk_kwargs),
        **kwargs)
    model.default_cfg = default_cfg
    if pretrained:
        load_pretrained(model, default_cfg, num_classes, in_chans)
    return model


@register_model
def skresnet18(pretrained=False, num_classes=1000, in_chans=3, **kwargs):
    """Constructs a ResNet-18 model.
    """
    default_cfg = default_cfgs['skresnet18']
    sk_kwargs = dict(
        min_attn_channels=16,
    )
    model = ResNet(
        SelectiveKernelBasic, [2, 2, 2, 2], num_classes=num_classes, in_chans=in_chans,
        block_args=dict(sk_kwargs=sk_kwargs), **kwargs)
    model.default_cfg = default_cfg
    if pretrained:
        load_pretrained(model, default_cfg, num_classes, in_chans)
    return model


@register_model
def sksresnet18(pretrained=False, num_classes=1000, in_chans=3, **kwargs):
    """Constructs a ResNet-18 model.
    """
    default_cfg = default_cfgs['skresnet18']
    sk_kwargs = dict(
        min_attn_channels=16,
        split_input=True
    )
    model = ResNet(
        SelectiveKernelBasic, [2, 2, 2, 2], num_classes=num_classes, in_chans=in_chans,
        block_args=dict(sk_kwargs=sk_kwargs), **kwargs)
    model.default_cfg = default_cfg
    if pretrained:
        load_pretrained(model, default_cfg, num_classes, in_chans)
    return model