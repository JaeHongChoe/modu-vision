"""Real CPU inference fixtures; deterministic weights carry no quality claim."""
from pathlib import Path
import hashlib
import torch
from backend.engine.classification.model import create_classification_model


def real_classification_checkpoints(models):
    model=create_classification_model(backbone='resnet18',num_classes=2,pretrained=False)
    with torch.no_grad():
        for value in model.parameters():value.zero_()
        model.fc.bias.copy_(torch.tensor([3.,0.]))
    for checkpoint in models.values():
        torch.save({'task':'classification','backbone':'resnet18','classes':['OK','NG'],
                    'image_size':[32,32],'model_state_dict':model.state_dict()},checkpoint)


def cohort_receipt(package,graph,checkpoints,images):
    from backend.engine.flow_package import verify_flow_parity_cohort,write_parity_receipt
    previous=torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        report=verify_flow_parity_cohort(package_dir=Path(package),pipeline=graph,checkpoints=checkpoints,
                                       images=images,device='cpu')
    finally:torch.set_num_threads(previous)
    assert report['status']=='passed',report
    assert all(row['packaged']['final_verdict'] in ('OK','NG') for row in report['images']),report
    write_parity_receipt(Path(package),report)
    return report


def bind_policy(policy,package):
    return {**policy,'device':'cpu','parity_receipt_sha256':hashlib.sha256((Path(package)/'parity_receipt.json').read_bytes()).hexdigest()}
