import argparse
import random
import time

import pandas as pd
import SimpleITK as sitk
import torch
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader
from tqdm import tqdm

from cacheio.Dataset import Compose, RegularDataset, LoadImage, Clip, ForeNormalize, ToTensor
from utils import tup_to_dict, expand_as_one_hot, get_fold_from_json
from visualizers.batch_visualizer import *
from metrics import dice, hausdorff_distance_95, avg_surface_distance_symmetric

from models.unet import UNet3D
from models.unet_deep_sup import UNetDeepSup

from models.unet_nine_layers.unet_l9 import UNetL9
from models.unet_nine_layers.unet_l9_deep_sup import UNetL9DeepSup
from models.unet_nine_layers.unet_l9_deep_sup_edge import UNetL9DeepSupEdge
from models.unet_nine_layers.unet_l9_deep_sup_edge_skip import UNetL9DeepSupEdgeSkip
from models.unet_nine_layers.unet_l9_deep_sup_rfp_multi_head import UNetL9DeepSupRFP_MultiHead
from models.unet_nine_layers.unet_l9_deep_sup_full_scheme import UNetL9DeepSupFullScheme

from models.unet_plusplus.unet_plusplus import UNetPlusPlus
from models.cascaded_vnet.cascaded_vnet_fnl_output import CascadedNetworksFnlOutput
from models.attention_unet.unet_CT_single_att_3D import unet_CT_single_att_3D
from models.attention_unet.unet_CT_single_att_dsv_3D import unet_CT_single_att_dsv_3D
from models.cascaded_vnet.vnet_kernel_size_3 import VNetKnl3

from models.unet_nine_layers.unet_l9_deep_sup_full_scheme_no_dags import UNetL9DeepSupFullScheme_NoDAGs
from models.unet_nine_layers.unet_l9_deep_sup_full_scheme_num_of_dags import UNetL9DeepSupFullScheme_NumDAGs
from models.unet_nine_layers.unet_l9_deep_sup_full_scheme_num_of_escs import UNetL9DeepSupFullScheme_ESCs

parser = argparse.ArgumentParser()
parser.add_argument('--gpu', type=str, default='0')
parser.add_argument('--fold', type=int, default=0)
parser.add_argument('--batch_size', type=int, default=1)
parser.add_argument('--net', type=str, default='unet_l9_ds_full_scheme_8_neighbor')  # TODO
parser.add_argument('--init_channels', type=int, default=16)
parser.add_argument('--optim', type=str, default='adam')
parser.add_argument('--lr', type=float, default=1e-3)
parser.add_argument('--N', type=int, default=-1)
parser.add_argument('--momentum', type=float, default=0.9)  # for SGD
parser.add_argument('--weight_decay', type=float, default=3e-4)
parser.add_argument('--num_class', type=int, default=9)
parser.add_argument('--organs', type=list, default=['bg', 'spleen', 'left kidney', 'gallbladder', 'esophagus', 'liver', 'stomach', 'pancreas', 'duodenum'])
parser.add_argument('--num_epoch', type=int, default=400)
parser.add_argument('--seed', default=1234, type=int, help='seed for initializing training.')
parser.add_argument('--resume', default=False, action='store_true')
parser.add_argument('--beta', type=float, default=1.)  # for DSC
parser.add_argument('--beta2', type=float, default=1.)  # for edge
# parser.add_argument('--out_fd', type=str, default='./results/unet_fold0')
parser.add_argument('--cv_json', type=str, default='/raid/yzf/data/abdominal_ct/cv_high_resolution.json')

args = parser.parse_args()
os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
PARENT_FOLD = 'UNet_9_Layer_Full_Scheme_8_Neighbor'  # TODO
CHILD_FOLD = f'unet_deep_sup_full_scheme_8_neigh_fold{args.fold}'  # TODO

args.ckp_file = f'./output/{PARENT_FOLD}/{CHILD_FOLD}/model_best.pth.tar'
# args.ckp_file = f'./output/{PARENT_FOLD}/{CHILD_FOLD}/checkpoint.pth.tar'

def get_model(args):
    model = None
    if args.net == 'unet_l9':
        model = UNetL9(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds':
        model = UNetL9DeepSup(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_edge':
        model = UNetL9DeepSupEdge(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_edge_skip':
        model = UNetL9DeepSupEdgeSkip(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_rfp_head':
        model = UNetL9DeepSupRFP_MultiHead(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_full_scheme':
        model = UNetL9DeepSupFullScheme(1, args.num_class, init_ch=args.init_channels)

    elif args.net == 'unet_l7':
        model = UNet3D(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_l7_ds':
        model = UNetDeepSup(1, args.num_class, init_ch=args.init_channels)

    elif args.net == 'attention_unet':
        model = unet_CT_single_att_dsv_3D(n_classes=args.num_class, in_channels=1)
    elif args.net == 'attention_unet_no_dsv':
        model = unet_CT_single_att_3D(n_classes=args.num_class, in_channels=1)
    elif args.net == 'vnet_knl3':
        model = VNetKnl3()
    elif args.net == 'unet_plusplus':
        model = UNetPlusPlus()
    elif args.net == 'cascaded_vnet':
        model = CascadedNetworksFnlOutput()

    elif args.net == 'unet_l9_ds_full_scheme_8_neighbor':
        model = UNetL9DeepSupFullScheme(1, args.num_class, num_neigh='eight', init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_full_scheme_no_dags':
        model = UNetL9DeepSupFullScheme_NoDAGs(1, args.num_class, init_ch=args.init_channels)
    elif args.net == 'unet_deep_sup_full_scheme_num_of_dags':
        model = UNetL9DeepSupFullScheme_NumDAGs(1, args.num_class, init_ch=args.init_channels)

    elif args.net == 'unet_l9_ds_full_scheme_0_escs':
        model = UNetL9DeepSupFullScheme_ESCs(1, args.num_class, no_escs=0, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_full_scheme_1_escs':
        model = UNetL9DeepSupFullScheme_ESCs(1, args.num_class, no_escs=1, init_ch=args.init_channels)
    elif args.net == 'unet_l9_ds_full_scheme_2_escs':
        model = UNetL9DeepSupFullScheme_ESCs(1, args.num_class, no_escs=2, init_ch=args.init_channels)
    elif args.net == 'unet_deep_sup_full_scheme_num_escs_no_dsup':
        model = UNetL9DeepSupFullScheme_ESCs(1, args.num_class, dsup=False, init_ch=args.init_channels)

    elif args.net == 'unet_l9_ds_rfp_head_8_neighbor':
        model = UNetL9DeepSupRFP_MultiHead(1, args.num_class, init_ch=args.init_channels, num_neigh='eight')

    if model is None:
        raise ValueError('Model is None.')
    return model

def add_edge_files(files_list):
    new_list = []
    for i in files_list:
        edge_file = i[0].replace('preproc_img', 'edge').replace('img', 'edge')
        tup = (i[0], i[1], edge_file)
        new_list.append(tup)
    return new_list

def parse_data(data):
    img_file = data['img_file']
    image = data['image']
    label = data['label']
    edge = data['edge']
    return img_file, image, label, edge

def get_dataloader(args):
    _, val_list = get_fold_from_json(args.cv_json, args.fold)
    t_val_list = add_edge_files(val_list)
    d_val_list = tup_to_dict(t_val_list)

    val_transforms = Compose([LoadImage(keys=['image', 'label', 'edge']),
                              Clip(keys=['image'], min=-250., max=200.),
                              ForeNormalize(keys=['image'], mask_key='label'),
                              ToTensor(keys=['image', 'label', 'edge'])])

    # Regular Dataset
    val_dataset = RegularDataset(data=d_val_list, transform=val_transforms)

    val_dataloader = DataLoader(val_dataset, batch_size=1, num_workers=2, shuffle=False)
    return val_dataloader

def inference(args):
    model = get_model(args).cuda()
    model.load_state_dict(torch.load(args.ckp_file)['state_dict'])
    model.eval()

    dsc_df, hd95_df, assd_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    dsc_ls, hd95_ls, assd_ls, index_ls = [], [], [], []

    val_dataloader = get_dataloader(args)
    for i, val_data in enumerate(val_dataloader):
        tic = time.time()
        case, volume, seg, _ = parse_data(val_data)
        spacing = sitk.ReadImage(case).GetSpacing()
        volume = volume.cuda()
        seg = seg.cuda()
        with torch.no_grad():
            if 'edge' in args.net:
                seg_score, _ = model(volume)
            elif 'rfp' in args.net or 'cascaded' in args.net:
                _, seg_score = model(volume)
            elif 'full_scheme' in args.net:
                _, seg_score, _ = model(volume)
            else:
                seg_score = model(volume)
        seg_probs = torch.softmax(seg_score, dim=1)
        seg_map = torch.argmax(seg_probs, dim=1, keepdim=True)

        case_dsc, case_hd95, case_assd = [], [], []
        tgt_oh = expand_as_one_hot(seg.squeeze(1).long(), args.num_class).cpu().numpy()  # target
        prd_oh = expand_as_one_hot(seg_map.squeeze(1).long(), args.num_class).cpu().numpy()  # prediction
        for cls in range(1, args.num_class):
            dsc = dice(test=prd_oh[:, cls, ...], reference=tgt_oh[:, cls, ...], nan_for_nonexisting=True)
            hd95 = hausdorff_distance_95(test=prd_oh[:, cls, ...], reference=tgt_oh[:, cls, ...], voxel_spacing=spacing, nan_for_nonexisting=True)
            assd = avg_surface_distance_symmetric(test=prd_oh[:, cls, ...], reference=tgt_oh[:, cls, ...], voxel_spacing=spacing, nan_for_nonexisting=True)

            case_dsc.append(dsc)
            case_hd95.append(hd95)
            case_assd.append(assd)

        simple_idx = '_'.join([case[0].split('/')[-3][:4], case[0].split('/')[-1][3:7]])
        index_ls.append(simple_idx)
        dsc_ls.append(case_dsc)
        hd95_ls.append(case_hd95)
        assd_ls.append(case_assd)

        # snapshot
        img = volume[0, 0].cpu().numpy()
        seg = seg[0, 0].cpu().numpy()
        seg_map = seg_map[0, 0].cpu().numpy()

        v_images = []
        h, w, d = img.shape
        for ind in range(d):
            im = np.rot90(img[..., ind])
            se = np.rot90(seg[..., ind])
            se_mp = np.rot90(seg_map[..., ind])

            im = (norm_score(im) * 255.).astype(np.uint8)
            imRGB = cv2.cvtColor(im, cv2.COLOR_GRAY2RGB)
            getScoreMap = lambda x: cv2.addWeighted(get_score_map(x, rang=(0, 8)),
                                                    0.8, imRGB, 0.2, 0)

            se = getScoreMap(se)
            se_mp = getScoreMap(se_mp)

            # add text in images. Occur bugs; im.copy() solves the problem.
            im = imtext(im.copy(), text='{:d} {:.2f}'.format(ind, np.nanmean(case_dsc) * 100),
                        space=(3, 10), color=(255,) * 3, thickness=2, fontScale=.6)

            h_images = [im, se, se_mp]
            v_images.append(imhstack(h_images, height=160))
        v_images = imvstack(v_images)

        imwrite(os.path.join(args.out_fd, 'snapshots', f'{simple_idx}.jpg'), v_images)

        # save prediction
        affine = nib.load(case[0]).affine
        nib_vol = nib.Nifti1Image(seg_map.astype(np.int32), affine)
        nib.save(nib_vol, os.path.join(args.out_fd, 'predictions', f'{simple_idx}.nii.gz'))

        print('Finish evaluating {}. Take {:.2f} s'.format(simple_idx, time.time() - tic))

    dsc_df = pd.DataFrame(dsc_ls, columns=args.organs[1:], index=index_ls)
    hd95_df = pd.DataFrame(hd95_ls, columns=args.organs[1:], index=index_ls)
    assd_df = pd.DataFrame(assd_ls, columns=args.organs[1:], index=index_ls)

    dsc_df.to_csv(os.path.join(args.out_fd, 'dsc.csv'))
    hd95_df.to_csv(os.path.join(args.out_fd, 'hd95.csv'))
    assd_df.to_csv(os.path.join(args.out_fd, 'assd.csv'))

if __name__ == '__main__':
    args.out_fd = f'./results/{args.net}_fold{args.fold}'

    os.makedirs(args.out_fd, exist_ok=False)
    os.makedirs(os.path.join(args.out_fd, 'predictions'))
    os.makedirs(os.path.join(args.out_fd, 'snapshots'))

    # record configurations
    with open(args.out_fd+'/parameters.txt', 'w') as txt:
        for a in vars(args).items():
            txt.write(str(a)+'\n')

    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True
    inference(args)