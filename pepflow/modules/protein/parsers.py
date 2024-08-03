import math
import torch
from Bio import PDB
from Bio.PDB import Selection
from Bio.PDB.Residue import Residue
from Bio.PDB.PDBParser import PDBParser
from Bio.PDB.MMCIFParser import MMCIFParser
from easydict import EasyDict

from pepflow.modules.protein.constants import (AA, max_num_heavyatoms, max_num_hydrogens,
                        restype_to_heavyatom_names, 
                        restype_to_hydrogen_names,
                        BBHeavyAtom, non_standard_residue_substitutions)

from Bio.PDB import PDBParser
from Bio.SeqUtils import seq1


def _get_residue_heavyatom_info(res: Residue):
    pos_heavyatom = torch.zeros([max_num_heavyatoms, 3], dtype=torch.float)
    mask_heavyatom = torch.zeros([max_num_heavyatoms, ], dtype=torch.bool)
    bfactor_heavyatom = torch.zeros([max_num_heavyatoms, ], dtype=torch.float)
    restype = AA(res.get_resname())
    for idx, atom_name in enumerate(restype_to_heavyatom_names[restype]):
        if atom_name == '': continue
        if atom_name in res:
            pos_heavyatom[idx] = torch.tensor(res[atom_name].get_coord().tolist(), dtype=pos_heavyatom.dtype)
            mask_heavyatom[idx] = True
            bfactor_heavyatom[idx] = res[atom_name].get_bfactor()
    return pos_heavyatom, mask_heavyatom, bfactor_heavyatom


def _get_residue_hydrogen_info(res: Residue):
    pos_hydrogen = torch.zeros([max_num_hydrogens, 3], dtype=torch.float)
    mask_hydrogen = torch.zeros([max_num_hydrogens, ], dtype=torch.bool)
    restype = AA(res.get_resname())

    for idx, atom_name in enumerate(restype_to_hydrogen_names[restype]):
        if atom_name == '': continue
        if atom_name in res:
            pos_hydrogen[idx] = torch.tensor(res[atom_name].get_coord().tolist(), dtype=pos_hydrogen.dtype)
            mask_hydrogen[idx] = True

    return pos_hydrogen, mask_hydrogen


def parse_pdb(path, model_id=0, unknown_threshold=1.0):
    parser = PDBParser()
    structure = parser.get_structure(None, path)
    return parse_biopython_structure(structure[model_id], unknown_threshold=unknown_threshold)


def parse_mmcif_assembly(path, model_id, assembly_id=0, unknown_threshold=1.0):
    parser = MMCIFParser()
    structure = parser.get_structure(None, path)
    mmcif_dict = parser._mmcif_dict
    if '_pdbx_struct_assembly_gen.asym_id_list' not in mmcif_dict:
        return parse_biopython_structure(structure[model_id], unknown_threshold=unknown_threshold)
    else:
        assemblies = [tuple(chains.split(',')) for chains in mmcif_dict['_pdbx_struct_assembly_gen.asym_id_list']]
        label_to_auth = {}
        for label_asym_id, auth_asym_id in zip(mmcif_dict['_atom_site.label_asym_id'], mmcif_dict['_atom_site.auth_asym_id']):
            label_to_auth[label_asym_id] = auth_asym_id
        model_real = list({structure[model_id][label_to_auth[ch]] for ch in assemblies[assembly_id]})
        return parse_biopython_structure(model_real)


def parse_biopython_structure(entity, unknown_threshold=1.0):
    chains = Selection.unfold_entities(entity, 'C')
    chains.sort(key=lambda c: c.get_id())
    data = EasyDict({
        'chain_id': [], 'chain_nb': [],
        'resseq': [], 'icode': [], 'res_nb': [],
        'aa': [],
        'pos_heavyatom': [], 'mask_heavyatom': [],
        # 'pos_hydrogen': [], 'mask_hydrogen': [],
        # 'bfactor_heavyatom': [],
    })
    tensor_types = {
        'chain_nb': torch.LongTensor,
        'resseq': torch.LongTensor,
        'res_nb': torch.LongTensor,
        'aa': torch.LongTensor,
        'pos_heavyatom': torch.stack,
        'mask_heavyatom': torch.stack,
        # 'bfactor_heavyatom': torch.stack,
        # 'pos_hydrogen': torch.stack,
        # 'mask_hydrogen': torch.stack,
    }

    count_aa, count_unk = 0, 0

    for i, chain in enumerate(chains):
        seq_this = 0   # Renumbering residues
        residues = Selection.unfold_entities(chain, 'R')
        residues.sort(key=lambda res: (res.get_id()[1], res.get_id()[2]))   # Sort residues by resseq-icode
        for _, res in enumerate(residues):
            resname = res.get_resname()
            if not AA.is_aa(resname): continue
            if not (res.has_id('CA') and res.has_id('C') and res.has_id('N')): continue
            restype = AA(resname)
            count_aa += 1
            if restype == AA.UNK: 
                count_unk += 1
                continue

            # Chain info
            data.chain_id.append(chain.get_id())
            data.chain_nb.append(i)

            # Residue types
            data.aa.append(restype) # Will be automatically cast to torch.long

            # Heavy atoms
            pos_heavyatom, mask_heavyatom, bfactor_heavyatom = _get_residue_heavyatom_info(res)
            data.pos_heavyatom.append(pos_heavyatom)
            data.mask_heavyatom.append(mask_heavyatom)
            # data.bfactor_heavyatom.append(bfactor_heavyatom)

            # Hydrogen atoms
            # pos_hydrogen, mask_hydrogen = _get_residue_hydrogen_info(res)
            # data.pos_hydrogen.append(pos_hydrogen)
            # data.mask_hydrogen.append(mask_hydrogen)

            # Sequential number
            resseq_this = int(res.get_id()[1])
            icode_this = res.get_id()[2]
            if seq_this == 0:
                seq_this = 1
            else:
                d_CA_CA = torch.linalg.norm(data.pos_heavyatom[-2][BBHeavyAtom.CA] - data.pos_heavyatom[-1][BBHeavyAtom.CA], ord=2).item()
                if d_CA_CA <= 4.0:
                    seq_this += 1
                else:
                    d_resseq = resseq_this - data.resseq[-1]
                    seq_this += max(2, d_resseq)

            data.resseq.append(resseq_this)
            data.icode.append(icode_this)
            data.res_nb.append(seq_this)

    if len(data.aa) == 0:
        return None, None

    if (count_unk / count_aa) >= unknown_threshold:
        return None, None

    seq_map = {}
    for i, (chain_id, resseq, icode) in enumerate(zip(data.chain_id, data.resseq, data.icode)):
        seq_map[(chain_id, resseq, icode)] = i

    for key, convert_fn in tensor_types.items():
        data[key] = convert_fn(data[key])
    
    # # ignore UNKNOWN residues and nobackbone residues, true for used residue
    # seq_mask = data['aa'] != AA.UNK
    # bb_mask = data['mask_heavyatom'][:, BBHeavyAtom.CA] & data['mask_heavyatom'][:, BBHeavyAtom.C] & data['mask_heavyatom'][:, BBHeavyAtom.N]
    # data['res_mask'] = seq_mask & bb_mask

    return data, seq_map
    

def get_fasta_from_pdb(pdb_file):
    parser = PDBParser()
    seq_dic = {}
    structure = parser.get_structure("structure_name", pdb_file)

    for model in structure:
        for chain in model:
            sequence = ""
            for residue in chain:
                if AA.is_aa(residue.get_resname()):
                    if residue.get_resname() == 'UNK':
                        sequence += 'X'
                    else:
                        sequence += PDB.Polypeptide.three_to_one(non_standard_residue_substitutions[residue.get_resname()])
            seq_dic[chain.id] = sequence

    return seq_dic

# def get_fasta_from_pdb(pdb_file):
#     parser = PDBParser()
#     structure = parser.get_structure("pdb", pdb_file)
    
#     fasta_sequence = ""
#     for chain in structure.get_chains():
#         for residue in chain.get_residues():
#             if residue.get_resname() in seq1(''):
#                 fasta_sequence += seq1(residue.get_resname())
    
#     return fasta_sequence


