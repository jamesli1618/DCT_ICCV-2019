from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import sys
import shutil
import os.path as osp
import random
from datetime import datetime
import numpy as np
import torchvision.transforms as transforms
import torch
from data.base_dataset import BaseDataset
from PIL import Image
from scipy import misc
import cv2
import pickle
from PIL import Image, ImageDraw
from data.data_preprocess import DataProcessor
import torch.utils.data as data
import copy
import torch


class UP3DDataset(BaseDataset):
    def __init__(self, opt):
        self.opt = opt
        self.annotation_path = opt.up3d_anno_path
        self.isTrain = opt.isTrain
        self.refine_IUV = opt.refine_IUV
        self.dp_num_max = opt.dp_num_max

        data_list = self.load_annotation(self.annotation_path)
        data_list = sorted(data_list, key=lambda a: a['image_path'])
        self.update_path(opt.data_root, data_list)
        self.data_list = data_list

        if not opt.isTrain:
            add_num = opt.batchSize - len(self.data_list)%opt.batchSize
            self.data_list += self.data_list[:add_num]
        
        transform_list = [transforms.ToTensor(),
                          transforms.Normalize((0.5, 0.5, 0.5),
                                               (0.5, 0.5, 0.5))]
        self.transform = transforms.Compose(transform_list)
        self.data_processor = DataProcessor(opt)


    def preprocess_data(self, img, IUV, kps, dp_kps, smpl_pose):
        # pad and resize
        img, kps, kps_weight, IUV, dp_kps, dp_kps_weight = \
            self.data_processor.padding_and_resize(img, kps, IUV, dp_kps)
        # random flip, only do in training phase
        # two '_' stands for joints_3d and smpl, which coco dataset does not contain
        if self.isTrain:
            img, kps, kps_weight, IUV, \
                dp_kps, dp_kps_weight, _, smpl_pose, flipped = \
                self.data_processor.random_flip(
                    img, kps, kps_weight, IUV, dp_kps, dp_kps_weight, None, smpl_pose)
        else:
            flipped = False
        # resize the keypoints to be in [-1, 1]
        kps, dp_kps = self.data_processor.normalize_keypoints(kps, dp_kps)
        return img, IUV, kps, kps_weight, dp_kps, dp_kps_weight, smpl_pose, flipped


    def preprocess_dp_anno(self, vert_indices, barycentric_coords):
        assert(vert_indices.shape[0] == barycentric_coords.shape[0])
        valid_point_num = vert_indices.shape[0]*3

        new_vert_indices = np.zeros((self.dp_num_max*3, ), dtype=int)
        new_vert_indices[:valid_point_num] = vert_indices.reshape(-1)

        new_bc_coords = np.zeros((self.dp_num_max*3, ))
        new_bc_coords[:valid_point_num] = barycentric_coords.reshape(-1)
        new_bc_coords = new_bc_coords.reshape(-1, 1)
        new_bc_coords = np.concatenate([new_bc_coords, new_bc_coords], axis=1)

        return new_vert_indices, new_bc_coords


    def __getitem__(self, index):
        # load raw data
        single_data = self.data_list[index]
        # image
        img_path = single_data['image_path']
        if not self.refine_IUV:
            iuv_path = single_data['IUV_path']
        else:
            iuv_path = single_data['IUV_refined_path']
        # other data
        keypoints = single_data['joints_2d']
        smpl_pose = single_data['smpl_pose']
        smpl_shape = single_data['smpl_shape']
        dp_x = single_data['dp_x']
        dp_y = single_data['dp_y']
        dp_keypoints = np.array([[x, y] for x, y in zip(dp_x, dp_y)])

        # open image and prepare keypoints
        img = cv2.imread(img_path)
        IUV = cv2.imread(iuv_path)
        keypoints = keypoints.T

        # preprocess the images and the corresponding annotation
        # dp_kps stands for densepose keypoints (dense keypoint)
        img, IUV, kps, kps_weight, dp_kps, dp_kps_weight, smpl_pose, flipped = \
            self.preprocess_data(img, IUV, keypoints, dp_keypoints, smpl_pose)
        
        # refine dense keypoints, found those dense keypoints which are still in the refined IUV map
        if self.refine_IUV:
            dp_kps_weight = self.data_processor.refine_dp_kps(
                cv2.imread(iuv_path), dp_keypoints, dp_kps_weight)

        # prepare dense keypoints after refinement (if applicable)
        if flipped:
            vert_indices = single_data['smpl_vert_indices_flipped']
            barycentric_coords = single_data['barycentric_coords_flipped']
        else:
            vert_indices = single_data['smpl_vert_indices']
            barycentric_coords = single_data['barycentric_coords']
        vert_indices, bc_coords = self.preprocess_dp_anno(
            vert_indices, barycentric_coords)
        # if the number of valid dense keypoints is less than 10, then abandon all of them
        if dp_keypoints.shape[0] < 10:
            dp_kps_weight = np.zeros((self.dp_num_max, 2), dtype=np.float32)

        # change numpy.array to torch.tensor
        img = self.transform(img).float()
        IUV = self.data_processor.transform_IUV(IUV).float()
        # keypoints
        kps = torch.from_numpy(kps).float()
        kps_weight = torch.from_numpy(kps_weight).float()
        # dense keypoints
        dp_kps = torch.from_numpy(dp_kps).float()
        dp_kps_weight = torch.from_numpy(dp_kps_weight).float()
        # only LongTensor could be used as index
        vert_indices = torch.from_numpy(vert_indices).long()
        bc_coords = torch.from_numpy(bc_coords).float()
        data = dict(
            img=img,
            IUV=IUV,
            keypoints=kps,
            keypoints_weights=kps_weight,
            dp_keypoints=dp_kps,
            dp_keypoints_weights=dp_kps_weight,
            vert_indices=vert_indices,
            bc_coords=bc_coords,
            index=torch.tensor(index)
        )
        
        if self.opt.up3d_use3d or not self.opt.isTrain:
            # smpl params
            smpl_pose = torch.from_numpy(smpl_pose).float()
            smpl_shape = torch.from_numpy(smpl_shape).float()
            smpl_params_weight = np.ones((1,), dtype=np.float32)
            smpl_params_weight = torch.from_numpy(smpl_params_weight).float()
            data['smpl_shape'] = smpl_shape
            data['smpl_pose'] = smpl_pose
            data['smpl_params_weight'] = smpl_params_weight

        return data


    def getitem(self, index):
        return self.__getitem__(index)

    def __len__(self):
        return len(self.data_list)

    @property
    def name(self):
        return 'UP3DDataset'
