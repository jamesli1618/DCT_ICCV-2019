
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os, sys, shutil
import os.path as osp
import time
from datetime import datetime
import torch
import numpy
import random
from options.test_options import TestOptions
from data.data_loader import CreateDataLoader
from models.dct_model import DCTModel
from util.visualizer import Visualizer
from util.evaluator import Evaluator
import cv2
import numpy as np
import util.ry_utils as ry_utils
import pdb

class Timer(object):
    def __init__(self, num_batch):
        self.start=time.time()
        self.num_batch = num_batch
    
    def click(self, batch_id):
        start, num_batch = self.start, self.num_batch
        end = time.time()
        cost_time = (end-start)/60
        speed = (batch_id+1)/cost_time
        res_time = (num_batch-(batch_id+1))/speed
        print("we have process {0}/{1}, it takes {2:.3f} mins, remain needs {3:.3f} mins".format(
            (batch_id+1), num_batch, cost_time, res_time))
        sys.stdout.flush()


def main():
    opt = TestOptions().parse()
    opt.serial_batches = True  # no shuffle
    opt.no_flip = True  # no flip
    visualize_eval = opt.visualize_eval
    opt.process_rank = -1

    data_loader = CreateDataLoader(opt)
    dataset = data_loader.load_data()
    assert(len(dataset.dataset.all_datasets) == 1)
    test_dataset = dataset.dataset.all_datasets[0]
    evaluator = Evaluator(test_dataset.data_list, opt.model_root)

    test_res_dir = 'evaluate_results'
    ry_utils.renew_dir(test_res_dir)

    epoch = 'latest'
    evaluator.clear()
    opt.which_epoch = str(epoch)
    model = DCTModel(opt)
    model.eval()

    timer = Timer(len(dataset))
    for i, data in enumerate(dataset):
        model.set_input(data)
        model.test()
        losses = model.compute_loss()
        pred_res = model.get_pred_result()
        data_idxs = data['index'].numpy()
        evaluator.update(data_idxs, losses, pred_res)
        timer.click(i)

    evaluator.remove_redunc()

    res_pkl_file = osp.join(test_res_dir, 'estimator_{}.pkl'.format(epoch))
    evaluator.save_to_pkl(res_pkl_file)
    backup_pkl_file = osp.join(test_res_dir, 'estimator.pkl')
    shutil.copy2(res_pkl_file, backup_pkl_file)

    print("Test of epoch: {} complete".format(epoch))
    print("PVE:{}".format(evaluator.pve))
    print("MPJPE:{}".format(evaluator.mpjpe))
    print("PVE-TPose:{}".format(evaluator.pve_tpose))
    print('------------------')
    sys.stdout.flush()


if __name__ == '__main__':
    main()