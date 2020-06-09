
import oneflow as flow
import numpy as np
import imageio
import glob
import os
import layers
import matplotlib.pyplot as plt

class DCGAN():

    def __init__(self):
        self.batch_size = 32
        self.z_dim = 100
        self.lr = 1e-4
        self.eval_interval = 400
        self.eval_size = 16
        self.seed = np.random.normal(0, 1, size=(self.eval_size, self.z_dim)).astype(np.float32)

    def compare_with_tf(self):
        from tf_dcgan import test
        tf_dcgan_test()
        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())
        func_config.train.primary_lr(self.lr)
        func_config.train.model_update_conf(dict(naive_conf={}))

        @flow.function(func_config)
        def TestJob(z=flow.FixedTensorDef((self.batch_size, 100)),
                    label1=flow.FixedTensorDef((self.batch_size, 1)),):
            img = self._generator(z, const_init=True)
            logit = self._discriminator(img, const_init=True)
            loss = flow.nn.sigmoid_cross_entropy_with_logits(label1, logit)
            flow.losses.add_loss(loss)
            return loss

        check_point = flow.train.CheckPoint()
        check_point.init()

        z = np.load("z.npy")
        label1 = np.ones((self.batch_size, 1)).astype(np.float32)
        label0 = np.zeros((self.batch_size, 1)).astype(np.float32)
        of_out = TestJob(z, label1).get()
        tf_out = np.load("out.npy")

        print((tf_out-of_out).mean())
        assert np.allclose(of_out.ndarray(), tf_out, rtol=1e-2, atol=1e-2)
    
    def train(self, epochs=1, model_dir=None, save=False):
        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())
        func_config.train.primary_lr(self.lr)
        func_config.train.model_update_conf(dict(adam_conf={"beta1":0.5}))

        @flow.function(func_config)
        def train_generator(z=flow.FixedTensorDef((self.batch_size, self.z_dim)),
                            label1=flow.FixedTensorDef((self.batch_size, 1))):        
            g_out = self._generator(z, trainable=True)
            g_logits = self._discriminator(g_out, trainable=False)
            g_loss = flow.nn.sigmoid_cross_entropy_with_logits(label1, g_logits, name="Gloss_sigmoid_cross_entropy_with_logits")
            g_loss = flow.math.reduce_mean(g_loss)

            flow.losses.add_loss(g_loss)
            return g_loss, g_out
        
        @flow.function(func_config)
        def train_discriminator(z=flow.FixedTensorDef((self.batch_size, 100)),
                                images=flow.FixedTensorDef((self.batch_size, 1, 28, 28)),
                                label1=flow.FixedTensorDef((self.batch_size, 1)),
                                label0=flow.FixedTensorDef((self.batch_size, 1))):
            g_out = self._generator(z, trainable=False)
            g_logits = self._discriminator(g_out, trainable=True)
            d_loss_fake = flow.nn.sigmoid_cross_entropy_with_logits(label0, g_logits, name="Dloss_fake_sigmoid_cross_entropy_with_logits")

            d_logits = self._discriminator(images, trainable=True, reuse=True)
            d_loss_real = flow.nn.sigmoid_cross_entropy_with_logits(label1, d_logits, name="Dloss_real_sigmoid_cross_entropy_with_logits")
            d_loss = d_loss_fake + d_loss_real
            d_loss = flow.math.reduce_mean(d_loss)
            flow.losses.add_loss(d_loss)
    
            return d_loss, d_loss_fake, d_loss_real
        
        func_config = flow.FunctionConfig()
        func_config.default_data_type(flow.float)
        func_config.default_distribute_strategy(flow.distribute.consistent_strategy())
        @flow.function(func_config)
        def eval_generator(z=flow.FixedTensorDef((self.eval_size, self.z_dim)),):
            g_out = self._generator(z, trainable=False)
            return g_out

        check_point = flow.train.CheckPoint()
        check_point.init()

        x, _ = self._load_mnist()
        batch_num = len(x) // self.batch_size

        for epoch_idx in range(epochs):
            for batch_idx in range(batch_num):
                z = np.random.normal(0, 1, size=(self.batch_size, self.z_dim)).astype(np.float32)
                label1 = np.ones((self.batch_size, 1)).astype(np.float32)
                label0 = np.zeros((self.batch_size, 1)).astype(np.float32)
                images = x[batch_idx*self.batch_size:(batch_idx+1)*self.batch_size].astype(np.float32)
                d_loss, _, _ = train_discriminator(z, images, label1, label0).get()
                g_loss, _ = train_generator(z, label1).get()
                
                batch_total = batch_idx + epoch_idx * batch_num * self.batch_size
                if (batch_idx + 1) % self.eval_interval == 0:
                    print("{}th epoch, {}th batch, dloss:{:>12.6f}, gloss:{:>12.6f}".format(epoch_idx+1, batch_idx+1, d_loss.mean(), g_loss.mean()))            
                    self._eval_model_and_save_images(eval_generator, batch_idx+1, epoch_idx+1)
    
    def save_to_gif(self):
        anim_file = 'dcgan.gif'
        with imageio.get_writer(anim_file, mode='I') as writer:
            filenames = glob.glob('gout/image*.png')
            filenames = sorted(filenames)
            last = -1
            for i,filename in enumerate(filenames):
                frame = 2*(i**0.5)
                if round(frame) > round(last):
                    last = frame
                else:
                    continue
                image = imageio.imread(filename)
                writer.append_data(image)
            image = imageio.imread(filename)
            writer.append_data(image)

    def _eval_model_and_save_images(self, model, batch_idx, epoch_idx):
        results = model(self.seed).get()
        fig = plt.figure(figsize=(4,4))
        for i in range(self.eval_size):
            plt.subplot(4, 4, i+1)
            plt.imshow(results[i, 0, :, :] * 127.5 + 127.5, cmap='gray')
            plt.axis('off')
        if not os.path.exists("gout"):
            os.mkdir("gout")
        plt.savefig('gout/image_{:02d}_{:04d}.png'.format(epoch_idx, batch_idx))

    def _generator(self, z, const_init=False, trainable=True):
        # (n, 256, 7, 7)
        h0 = layers.dense(z, 7 * 7 * 256, name='g_fc1', 
                          const_init=const_init, trainable=trainable)
        h0 = layers.batchnorm(h0, axis=1, name='g_bn1')
        h0 = flow.nn.leaky_relu(h0, 0.3)
        h0 = flow.reshape(h0, (-1, 256, 7, 7))
        # (n, 128, 7, 7)
        h1 = layers.deconv2d(h0, 128, 5, strides=1, name='g_deconv1', 
                             const_init=const_init, trainable=trainable)
        h1 = layers.batchnorm(h1, name='g_bn2')
        h1 = flow.nn.leaky_relu(h1, 0.3)
        # (n, 64, 14, 14)
        h2 = layers.deconv2d(h1, 64, 5, strides=2, name='g_deconv2',
                             const_init=const_init, trainable=trainable)
        h2 = layers.batchnorm(h2, name='g_bn3')
        h2 = flow.nn.leaky_relu(h2, 0.3)
        # (n, 1, 28, 28)
        out = layers.deconv2d(h2, 1, 5, strides=2, name='g_deconv3',
                              const_init=const_init, trainable=trainable)
        out = flow.keras.activations.tanh(out)
        return out
    
    def _discriminator(self, img, const_init=False, trainable=True, reuse=False):
        # (n, 1, 28, 28)
        h0 = layers.conv2d(img, 64, 5, name='d_conv1', const_init=const_init,
                           trainable=trainable, reuse=reuse)
        h0 = flow.nn.leaky_relu(h0, 0.3)
        h0 = flow.nn.dropout(h0, rate=0.3)
        # (n, 64, 14, 14)
        h1 = layers.conv2d(h0, 128, 5, name='d_conv2', const_init=const_init,
                           trainable=trainable, reuse=reuse)
        h1 = flow.nn.leaky_relu(h1, 0.3)
        h1 = flow.nn.dropout(h1, rate=0.3)
        # (n, 128 * 7 * 7)
        out = flow.reshape(h1, (self.batch_size, -1))
        # (n, 1)
        out = layers.dense(out, 1, name='d_fc', const_init=const_init,
                           trainable=trainable, reuse=reuse)
        return out

    def _load_mnist(self, data_dir='./data', dataset_name='mnist', transpose=True):
        data_dir = os.path.join(data_dir, dataset_name)
        
        fd = open(os.path.join(data_dir,'train-images-idx3-ubyte'))
        loaded = np.fromfile(file=fd,dtype=np.uint8)
        trX = loaded[16:].reshape((60000,28,28,1)).astype(np.float)

        fd = open(os.path.join(data_dir,'train-labels-idx1-ubyte'))
        loaded = np.fromfile(file=fd,dtype=np.uint8)
        trY = loaded[8:].reshape((60000)).astype(np.float)

        fd = open(os.path.join(data_dir,'t10k-images-idx3-ubyte'))
        loaded = np.fromfile(file=fd,dtype=np.uint8)
        teX = loaded[16:].reshape((10000,28,28,1)).astype(np.float)

        fd = open(os.path.join(data_dir,'t10k-labels-idx1-ubyte'))
        loaded = np.fromfile(file=fd,dtype=np.uint8)
        teY = loaded[8:].reshape((10000)).astype(np.float)

        trY = np.asarray(trY)
        teY = np.asarray(teY)
        
        X = np.concatenate((trX, teX), axis=0)
        y = np.concatenate((trY, teY), axis=0).astype(np.int)
        
        seed = 547
        np.random.seed(seed)
        np.random.shuffle(X)
        np.random.seed(seed)
        np.random.shuffle(y)
        
        y_vec = np.zeros((len(y), 10), dtype=np.float)
        for i, label in enumerate(y):
            y_vec[i,y[i]] = 1.0
        
        if transpose:
            X = np.transpose(X, (0,3,1,2))

        return X/255., y_vec

if __name__ == '__main__':
    os.environ['ENABLE_USER_OP']='True'
    dcgan = DCGAN()
    # dcgan.compare_with_tf()
    dcgan.train(epochs=2)
    dcgan.save_to_gif()