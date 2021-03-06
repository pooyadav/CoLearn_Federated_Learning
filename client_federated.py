import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import asyncio
import time

import syft as sy
from syft.workers import websocket_client
# from syft.frameworks.torch.federated import utils
import settings
from datasets import NetworkTrafficDataset, ToTensor

# This is important to exploit the GPU if it is available
use_cuda = torch.cuda.is_available()

# Seed for the random number generator
torch.manual_seed(1)

device = torch.device("cuda" if use_cuda else "cpu")

class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.fc1 = nn.Linear(28 * 28, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 10)

    def forward(self, x):
        x = x.view(-1, 28 * 28)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x


class TestingRemote(nn.Module):
    def __init__(self):
        super(TestingRemote, self).__init__()
        self.fc1 = nn.Linear(2, 50)
        self.fc2 = nn.Linear(50, 10)
        self.fc3 = nn.Linear(10, 1)
    
    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x

class FFNN(nn.Module):
    """
    Simple Binary FeedForward neural network
    """
    def __init__(self):
        super(FFNN, self).__init__()
        self.fc1 = nn.Linear(10, 50)
        self.fc2 = nn.Linear(50, 30)
        self.fc3 = nn.Linear(30, 10)
        self.fc4 = nn.Linear(10, 1)
    
    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = F.relu(self.fc3(x))
        x = torch.sigmoid(self.fc4(x))
        return x
    
    def get_traced_model(self):
        return torch.jit.trace(self, torch.zeros(10))


# Loss function
# it needs to be serializable. 
#  We can define a usual function just changing it to use jit.
# In this case is the mean square error
@torch.jit.script
def loss_fn(target, pred):
    # return ((target.view(pred.shape).float() - pred.float()) ** 2).mean()
    return F.binary_cross_entropy(input=pred, target=target)


def train_local(worker, model, opt, epochs, federated_train_loader, args):
    # In this case the location of the worker is directly in the data
    """Send the model to the worker and fit the model on the worker's training data.
    Args:
        worker: train the model on that worker
        model: Model which shall be trained.
        opt: Optimization algorithm
        epochs: Number of epochs
        federated_train_loader: loader of the data distributed by us among the network
        args: value of the singleton class Arguments

    Returns:
        A tuple containing:
            * improved model: model after training at the worker.
            * loss: Loss on last training batch, torch.tensor.
    """
    model.train()
    result_models = {}
    for epoch in range(epochs):
        for batch_idx, (data, target) in enumerate(federated_train_loader): # now it is a distributed dataset
            if data.location.id == worker:
                model.send(data.location)

                # 1) Erase the previous gradients
                opt.zero_grad()

                # 2) Make a prediction
                pred = model(data)
                
                # 3) Calculate how much we missed
                loss = ((pred - target)**2).sum() # try to change this function
                
                # 4) figure out which weights caused us to miss
                loss.backward()

                # 5) change those weights
                opt.step()

                model.get()
                if batch_idx % args.log_interval == 0:
                    loss = loss.get() # <-- NEW: get the loss back
                    print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}'.format(
                        epoch, batch_idx * args.batch_size, len(federated_train_loader) * args.batch_size,
                        100. * batch_idx / len(federated_train_loader), loss.item()))
    
    return model, loss


def encrypted_training(args, model, private_train_loader, optimizer, epoch):
    """Training of an encrypted model
    Args:
        args: value of the singleton class Arguments
        model: Encrypted model Model which shall be trained
        private_train_loader: loader of the encrypted data distributed by us among the network
        optimizer: Optimization algorithm
        epochs: Number of epochs
        

    Returns:
        None
    """
    model.train()
    
    # 0) Compute the time of training for each epoch
    start_time = time.time()

    for batch_idx, (data, target) in enumerate(private_train_loader): # <-- now it is a private dataset
        
        # 1) Erase the previous gradients
        optimizer.zero_grad()
        
        # 2) Make a prediction
        output = model(data)
        
        # 3) Calculate how much we missed
        batch_size = output.shape[0]
        loss = ((output - target)**2).sum().refresh()/batch_size
        
        # 4) figure out which weights caused us to miss
        loss.backward()
        
        # 5) change those weights
        optimizer.step()

        if batch_idx % args.log_interval == 0:
            loss = loss.get().float_precision()
            print('Train Epoch: {} [{}/{} ({:.0f}%)]\tLoss: {:.6f}\tTime: {:.3f}s'.format(
                epoch, batch_idx * args.batch_size, len(private_train_loader) * args.batch_size,
                100. * batch_idx / len(private_train_loader), loss.item(), time.time() - start_time))
        
    


async def train_remote(
    worker: websocket_client.WebsocketClientWorker,
    traced_model: torch.jit.ScriptModule,
    batch_size: int,
    optimizer: str,
    max_nr_batches: int,
    epochs: int,
    lr: float,
    ):
    """Send the model to the worker and fit the model on the worker's training data.
    Args:
        worker: Remote location, where the model shall be trained.
        traced_model: Model which shall be trained.
        batch_size: Batch size of each training step.
        optimizer: name of the optimizer to be used
        max_nr_batches: If > 0, training on worker will stop at min(max_nr_batches, nr_available_batches).
        epochs: Number of epochs to perform remotely
        lr: Learning rate of each training step.
    Returns:
        A tuple containing:
            * worker_id: Union[int, str], id of the worker.
            * improved model: torch.jit.ScriptModule, model after training at the worker.
            * loss: Loss on last training batch, torch.tensor.
    """
    train_config = sy.TrainConfig(
        model=traced_model,
        loss_fn=loss_fn,
        batch_size=batch_size,
        shuffle=True,
        max_nr_batches=max_nr_batches,
        epochs=epochs,
        optimizer=optimizer,
        optimizer_args={"lr": lr},
    )
    train_config.send(worker)
    loss = await worker.async_fit(dataset_key="training", return_ids=[0])
    model = train_config.model_ptr.get().obj
     
    return worker.id, model, loss



def evaluate(model, test_loader, device):
    """Evaluate the model. This method can be used only for a local evaluation of the global model
    Args:
        model: model to evaluate
        args: parameters for the evaluation (see class Arguments)
        test_loader: loader for the data to test
        device: enable the possibility to exploit the GPU
    Returns:
        no return
    """
    print("Local evaluation start...")
    model.eval()


    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            out = model(data)
            test_loss += F.binary_cross_entropy(input=out, target=target).item() # Apply binary cross entropy for our binary nn
            pred = torch.round(out) # Approximate the value to 1 or 0 to compute the correctiness of this prediction
            # pred = output.argmax(1, keepdim=True) # get the index of the max log-probability
            temp = pred.eq(target.view_as(pred)).sum().item()
            if temp == 1:
                correct += pred.eq(target.view_as(pred)).sum().item()
            else:
                print("Prediction uncorrect: " + str(pred))
                print("Data: ")
                print(data)
                print("Target: ")
                print(target)
    
    test_loss /= len(test_loader.dataset)
    print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.5f}%)\n'.format(
        test_loss, correct, len(test_loader.dataset),
        100. * correct / len(test_loader.dataset)))


# For model encryption --> only training
def get_private_data_loaders(workers, args, n_train_items, precision_fractional=3, crypto_provider=None):
    
    def secret_share(tensor):
        """
        Transform to fixed precision and secret share a tensor
        """
        return (
            tensor
            .fix_precision(precision_fractional=precision_fractional)
            .share(*workers, crypto_provider=crypto_provider, requires_grad=True)
        )
    
    train_loader = torch.utils.data.DataLoader(NetworkTrafficDataset(args.test_path, transform=ToTensor()), shuffle=True)
    
    result_train_loader = [
        (secret_share(data), secret_share(target))
        for i, (data, target) in enumerate(train_loader)
        if i < n_train_items / args.batch_size
    ]
    
    return result_train_loader

