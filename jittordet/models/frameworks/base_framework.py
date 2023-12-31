# Modified from OpenMMLab mmdet/models/base.py
# Copyright (c) OpenMMLab. All rights reserved.
from abc import ABCMeta, abstractmethod
from collections import OrderedDict
from typing import Dict, List, Tuple, Union

import jittor as jt
import jittor.nn as nn

from jittordet.engine import MODELS
from jittordet.structures import (DetDataSample, InstanceList, OptSampleList,
                                  SampleList)
from jittordet.utils import is_list_of

ForwardResults = Union[Dict[str, jt.Var], List[DetDataSample], Tuple[jt.Var],
                       jt.Var]


class BaseFramework(nn.Module, metaclass=ABCMeta):
    """Base class for detectors.

    Args:
       data_preprocessor (dict or ConfigDict, optional): The pre-process
           config of :class:`BaseDataPreprocessor`.  it usually includes,
            ``pad_size_divisor``, ``pad_value``, ``mean`` and ``std``.
       init_cfg (dict or ConfigDict, optional): the config to control the
           initialization. Defaults to None.
    """

    def __init__(self, preprocessor=None):
        super().__init__()
        self.preprocessor = MODELS.build(preprocessor)

    @property
    def with_neck(self) -> bool:
        """bool: whether the detector has a neck"""
        return hasattr(self, 'neck') and self.neck is not None

    # TODO: these properties need to be carefully handled
    # for both single stage & two stage detectors
    @property
    def with_shared_head(self) -> bool:
        """bool: whether the detector has a shared head in the RoI Head"""
        return hasattr(self, 'roi_head') and self.roi_head.with_shared_head

    @property
    def with_bbox(self) -> bool:
        """bool: whether the detector has a bbox head"""
        return ((hasattr(self, 'roi_head') and self.roi_head.with_bbox)
                or (hasattr(self, 'bbox_head') and self.bbox_head is not None))

    @property
    def with_mask(self) -> bool:
        """bool: whether the detector has a mask head"""
        return ((hasattr(self, 'roi_head') and self.roi_head.with_mask)
                or (hasattr(self, 'mask_head') and self.mask_head is not None))

    def execute(self, data, phase: str = 'tensor') -> ForwardResults:
        """The unified entry for a forward process in both training and test.

        The method should accept three modes: "tensor", "predict" and "loss":

        - "tensor": Forward the whole network and return tensor or tuple of
        tensor without any post-processing, same as a common nn.Module.
        - "predict": Forward and return the predictions, which are fully
        processed to a list of :obj:`DetDataSample`.
        - "loss": Forward and return a dict of losses according to the given
        inputs and data samples.

        Note that this method doesn't handle either back propagation or
        parameter update, which are supposed to be done in :meth:`train_step`.

        Args:
            inputs (torch.Tensor): The input tensor with shape
                (N, C, ...) in general.
            data_samples (list[:obj:`DetDataSample`], optional): A batch of
                data samples that contain annotations and predictions.
                Defaults to None.
            phase (str): Return what kind of value. Defaults to 'tensor'.

        Returns:
            The return type depends on ``mode``.

            - If ``mode="tensor"``, return a tensor or a tuple of tensor.
            - If ``mode="predict"``, return a list of :obj:`DetDataSample`.
            - If ``mode="loss"``, return a dict of tensor.
        """
        data = self.preprocessor(data)
        inputs, data_samples = data['inputs'], data['data_samples']
        if phase == 'loss':
            results = self.loss(inputs, data_samples)
            return self.parse_losses(results)
        elif phase == 'predict':
            return self.predict(inputs, data_samples)
        elif phase == 'tensor':
            return self._execute(inputs, data_samples)
        else:
            raise RuntimeError(f'Invalid mode "{phase}". '
                               'Only supports loss, predict and tensor mode')

    def parse_losses(
            self, losses: Dict[str,
                               jt.Var]) -> Tuple[jt.Var, Dict[str, jt.Var]]:
        """Parses the raw outputs (losses) of the network.

        Args:
            losses (dict): Raw output of the network, which usually contain
                losses and other necessary information.

        Returns:
            tuple[Tensor, dict]: There are two elements. The first is the
            loss tensor passed to optim_wrapper which may be a weighted sum of
            all losses, and the second is log_vars which will be sent to the
            logger.
        """
        log_vars = []
        for loss_name, loss_value in losses.items():
            if isinstance(loss_value, jt.Var):
                log_vars.append([loss_name, loss_value.mean()])
            elif is_list_of(loss_value, jt.Var):
                log_vars.append(
                    [loss_name,
                     sum(_loss.mean() for _loss in loss_value)])
            else:
                raise TypeError(
                    f'{loss_name} is not a tensor or list of tensors')

        loss = sum(value for key, value in log_vars if 'loss' in key)
        log_vars.insert(0, ['loss', loss])
        log_vars = OrderedDict(log_vars)  # type: ignore

        return loss, log_vars  # type: ignore

    @abstractmethod
    def loss(self, batch_inputs: jt.Var,
             batch_data_samples: List[DetDataSample]) -> Union[dict, tuple]:
        """Calculate losses from a batch of inputs and data samples."""
        pass

    @abstractmethod
    def predict(
            self, batch_inputs: jt.Var,
            batch_data_samples: List[DetDataSample]) -> List[DetDataSample]:
        """Predict results from a batch of inputs and data samples with post-
        processing."""
        pass

    @abstractmethod
    def _execute(self,
                 batch_inputs: jt.Var,
                 batch_data_samples: OptSampleList = None):
        """Network forward process.

        Usually includes backbone, neck and head forward without any post-
        processing.
        """
        pass

    @abstractmethod
    def extract_feat(self, batch_inputs: jt.Var):
        """Extract features from images."""
        pass

    def add_pred_to_datasample(self, data_samples: SampleList,
                               results_list: InstanceList) -> SampleList:
        """Add predictions to `DetDataSample`.

        Args:
            data_samples (list[:obj:`DetDataSample`], optional): A batch of
                data samples that contain annotations and predictions.
            results_list (list[:obj:`InstanceData`]): Detection results of
                each image.

        Returns:
            list[:obj:`DetDataSample`]: Detection results of the
            input images. Each DetDataSample usually contain
            'pred_instances'. And the ``pred_instances`` usually
            contains following keys.

                - scores (Tensor): Classification scores, has a shape
                    (num_instance, )
                - labels (Tensor): Labels of bboxes, has a shape
                    (num_instances, ).
                - bboxes (Tensor): Has a shape (num_instances, 4),
                    the last dimension 4 arrange as (x1, y1, x2, y2).
        """
        for data_sample, pred_instances in zip(data_samples, results_list):
            data_sample.pred_instances = pred_instances
        return data_samples
