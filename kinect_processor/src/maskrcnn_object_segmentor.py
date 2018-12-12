#!/usr/bin/env python
"""
title           :maskrcnn_object_segmentor.py
description     :Takes the processed kinect data and extracts the points from each pointcloud
                :corresponding to an individual object using a pretrained Mask R-CNN model.
                :The result is saved under rosbag_dumps/segmented_objects.npz
author          :Yordan Hristov <yordan.hristov@ed.ac.uk
date            :11/2018
python_version  :2.7.6
==============================================================================
"""

import argparse
import struct
import copy
import time
import numpy as np
import cv2
import os
import os.path as osp
import json
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

import chainer
import chainer_mask_rcnn as cmr
import yaml
import pprint
import os.path as osp

class Object_Segmentor(object):

    def __init__(self, verbose=False, args=None):
        self.verbose = verbose
        self.output = []
        self.counter = 0
        self.args = args
        self.cutoff = args.cutoff
        self.scene = args.scene

        config_file = open(osp.join('scenes', self.scene, 'config.json'), "r")
        self.expected_objects = json.load(config_file)['expected_objects']

        self.bounds = {'x':[0.1, 1.3], 'y':[-0.5, 0.5], 'z':[0.0, 1.0]}



    def process_data(self):

        height = 540
        width = 960
        offset = 0
        margin_h = 0
        margin_w = 0
        
        image_bbox = [offset + margin_h, \
                      offset + height - margin_h, \
                      offset + margin_w, \
                      offset + width - margin_w]

        height -= 2*margin_h
        width -= 2*margin_w

        # half of the crop's size; we assume a square crop
        crop_size = 100

        # params for the bg subraction
        bg_patch_size = 10
        bg_threshold = 40

        for (xyz, bgr) in self.data:
            
            self.counter += 1

            if self.counter >= self.cutoff:
                return
            print("{0} Clouds Segmented.".format(self.counter))

            xyz = xyz[image_bbox[0] : image_bbox[1], image_bbox[2] : image_bbox[3], :]
            bgr = bgr[image_bbox[0] : image_bbox[1], image_bbox[2] : image_bbox[3], :].astype(np.uint8)

            # bgr = cv2.imread("maskrcnn_model/frame0000.jpg")

            new_entry = {}
            #pass through the model
            batch = np.array(np.transpose(bgr, (2,0,1)))
            batch = batch[np.newaxis, :]
            bboxes, masks, labels, scores = self.mask_rcnn.predict(batch)

            indecies = scores[0] >= self.score_threshold
            bboxes = bboxes[0][indecies]
            masks = masks[0][indecies]
            labels = labels[0][indecies]
            labels = self.class_names[labels]
            print(labels)
            scores = scores[0][indecies]

            if not all([x in labels for x in self.expected_objects]):
                print("Only these objects were found - {0} with scores - {1}".format(labels, scores))
                cv2.imshow("bgr", bgr)
                cv2.waitKey(0)
                continue                

            # cv2.imshow("bgr", bgr)
            # for i in range(masks.shape[0]):
            #     box = bboxes[i]
            #     y1, x1, y2, x2 = box.astype(int).tolist()
            #     mask = masks[i,:,:].astype(np.uint8) * 255
            #     cv2.rectangle(mask,(x1, y1),(x2, y2),(128), 2)
            #     cv2.imshow("mask_" + str(i), mask)
            # cv2.waitKey(0)
            # continue
            
            for (bbox, mask, label, score) in zip(bboxes, masks, labels, scores):

                if label not in self.expected_objects:
                    continue

                y1, x1, y2, x2 = bbox.astype(int).tolist()
                cY = (y1 + y2) / 2
                cX = (x1 + x2) / 2
                bbox = [cY - crop_size, cY + crop_size, cX - crop_size, cX + crop_size]
                bbox = list(map(lambda x : 0 if x < 0 else x, bbox))

                maxes = np.repeat(image_bbox[1::2], 2)
                bbox = list(map(lambda (x, y) : y if x > y else x, zip(bbox, maxes)))

                xyz_crop = xyz[bbox[0] : bbox[1], bbox[2] : bbox[3], :].copy()
                xyz_crop_norm = self.normalise_xyz(xyz_crop, bounds=self.bounds)
                bgr_crop = bgr[bbox[0] : bbox[1], bbox[2] : bbox[3], :].copy()
                mask = mask[bbox[0] : bbox[1], bbox[2] : bbox[3]].copy()

                kernel = np.ones((5, 5), np.uint8)
                mask = cv2.erode(mask.astype(np.uint8), kernel, iterations=1)

                # bgr_crop[mask != 1] = 0
                # cv2.imshow(label, bgr_crop)
                # cv2.waitKey(0)
                # cv2.destroyAllWindows()
                # continue

                xyz_object = xyz_crop_norm.copy()
                xyz_object[mask != 1] = 0

                # print(np.max(xyz_object[...,2]))

                assert ((xyz_object <= 1).all() and (xyz_object >= 0).all()),\
                "The data can not be normalised in the range [0,1] - Potentially bad bounds"
                
                assert (xyz_object.shape == (crop_size * 2, crop_size * 2, 3)),\
                "For scene {0}, hue {1} has different than expected dimensionality {2} - {3}".format(self.counter, label, (crop_size * 2, crop_size * 2, 3), xyz_object.shape)

                if label not in new_entry.keys():
                    new_entry[label] = xyz_object

            assert len(new_entry.items()) == len(self.expected_objects), \
            "Some objects were not extracted for cloud # {0}! {1}".format(self.counter, new_entry.keys())
                   
            self.output.append(new_entry)

    def fill_holes_get_max_cnt(self, mask, fill=-1):
        """Given a binary mask, fills in any holes in the contours, selects the contour with max area
        and returns a mask with only it drawn + its bounding box
        """
        canvas = np.zeros(mask.shape, dtype=np.uint8)
        cnts, hier = cv2.findContours(mask.astype(np.uint8),cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
        areas = [cv2.contourArea(cnt) for cnt in cnts]

        # for (cnt, area) in sorted(zip(cnts, areas), key=lambda x: x[1], reverse=True):
        #     cv2.drawContours(canvas,[cnt],0,1,-1)

        (cnt, area) = sorted(zip(cnts, areas), key=lambda x: x[1], reverse=True)[0]
        cv2.drawContours(canvas,[cnt],0,1,fill)

        return canvas.astype(np.uint8)


    def normalise_xyz(self, xyz, bounds={}):
        
        xyz_norm = np.zeros(xyz.shape)
        xyz_norm[...,0] = (xyz[...,0] - bounds['x'][0]) / (bounds['x'][1] - bounds['x'][0])
        xyz_norm[...,1] = (xyz[...,1] - bounds['y'][0]) / (bounds['y'][1] - bounds['y'][0])
        xyz_norm[...,2] = (xyz[...,2] - bounds['z'][0]) / (bounds['z'][1] - bounds['z'][0])

        mask = np.logical_and((xyz_norm <= 1).all(axis=2), (xyz_norm >= 0).all(axis=2)).astype(np.uint8)
        mask = np.tile(mask.reshape(xyz_norm.shape[0], xyz_norm.shape[1], 1), (1, 1, 3))
        xyz_norm = xyz_norm * mask

        return xyz_norm


    def plot_xyz(self, xyz_points):

        xs = xyz_points[:,:,0][::5]
        ys = xyz_points[:,:,1][::5]
        zs = xyz_points[:,:,2][::5]

        fig = plt.figure()
        ax = fig.gca(projection='3d')

        ax.scatter(xs, ys, zs, c='c')
        
        ax.set_xlabel('X', fontsize='20', fontweight="bold")
        ax.set_xlim(-1, 1)
        ax.set_ylabel('Y', fontsize='20', fontweight="bold")
        ax.set_ylim(-1, 1)
        ax.set_zlabel('Z', fontsize='20', fontweight="bold")
        ax.set_zlim(0, 1)

        plt.show()


    def load_processed_rosbag(self):
        self.data = []
        file_names = [x for x in os.listdir(osp.join('scenes', str(self.scene))) if '.npz' in x]
        for file_name in file_names:
            
            print("Loading {0}".format(file_name))
            if self.data == []:
                self.data = np.load(osp.join('scenes', str(self.scene), file_name))['arr_0']
            else:
                self.data = np.append(self.data, np.load(osp.join('scenes', str(self.scene), file_name))['arr_0'], axis=0)
            print(self.data.shape)


    def save_to_npz(self, output_folder="scenes"):

        print("{0} clouds are saved in the npz array.".format(len(self.output)))
        path = osp.join(output_folder, str(self.scene) , 'segmented_objects.npz')
        np.savez(path, self.output)


    def load_model(self, folder_name="."):

        # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
        # param
        params = yaml.load(open(osp.join(folder_name, 'params.yaml')))
        print('Training config:')
        print('# ' + '-' * 77)
        pprint.pprint(params)
        print('# ' + '-' * 77)

        # dataset
        if 'class_names' in params:
            class_names = params['class_names']
        else:
            raise ValueError

        # model

        if params['dataset'] == 'voc':
            if 'min_size' not in params:
                params['min_size'] = 600
            if 'max_size' not in params:
                params['max_size'] = 1000
            if 'anchor_scales' not in params:
                params['anchor_scales'] = (1, 2, 4, 8, 16, 32)
        elif params['dataset'] == 'coco':
            if 'min_size' not in params:
                params['min_size'] = 800
            if 'max_size' not in params:
                params['max_size'] = 1333
            if 'anchor_scales' not in params:
                params['anchor_scales'] = (1, 2, 4, 8, 16, 32)
        else:
            assert 'min_size' in params
            assert 'max_size' in params
            assert 'anchor_scales' in params

        if params['pooling_func'] == 'align':
            pooling_func = cmr.functions.roi_align_2d
        elif params['pooling_func'] == 'pooling':
            pooling_func = cmr.functions.roi_pooling_2d
        elif params['pooling_func'] == 'resize':
            pooling_func = cmr.functions.crop_and_resize
        else:
            raise ValueError(
                'Unsupported pooling_func: {}'.format(params['pooling_func'])
            )

        model_name = [x for x in os.listdir(folder_name) if ".npz" in x][0]
        pretrained_model = osp.join(folder_name, model_name)
        print('Using pretrained_model: %s' % pretrained_model)

        model = params['model']
        self.mask_rcnn = cmr.models.MaskRCNNResNet(
            n_layers=int(model.lstrip('resnet')),
            n_fg_class=len(class_names),
            pretrained_model=pretrained_model,
            pooling_func=pooling_func,
            anchor_scales=params['anchor_scales'],
            mean=params.get('mean', (123.152, 115.903, 103.063)),
            min_size=params['min_size'],
            max_size=params['max_size'],
            roi_size=params.get('roi_size', 7),
        )
        
        self.class_names = np.array(class_names)
        self.score_threshold = 0.6

        # self.mask_rcnn.to_cpu()
        chainer.cuda.get_device_from_id(0).use()
        self.mask_rcnn.to_gpu()



if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Segment objects with Mask RCNN')
    parser.add_argument('--cutoff', default=100, type=int,
                        help='Number of frames to be captured')
    parser.add_argument('--scene', '-sc', default='0',
                        help='Index for a scene/setup')
    parser.add_argument('--dtype', default='train',
                        help='Work with seen ot unseen data')
    args = parser.parse_args()

    segmentor = Object_Segmentor(verbose=False, args=args)
    segmentor.load_processed_rosbag()
    segmentor.load_model("maskrcnn_model")
    segmentor.process_data()

    print("Final count: "+ str(segmentor.counter))
    segmentor.output = np.array(segmentor.output)
    segmentor.save_to_npz()
    print("NPZ saved")