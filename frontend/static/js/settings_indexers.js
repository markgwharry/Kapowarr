// Indexers Settings Page JavaScript
// Uses global url_base and fetchAPI/sendAPI from general.js

let indexers = [];
let editingIndexerId = null;

// Load indexers on page load
document.addEventListener('DOMContentLoaded', () => {
    usingApiKey().then(api_key => {
        loadIndexers(api_key);
        setupEventListeners(api_key);
    });
});

async function loadIndexers(api_key) {
    try {
        const response = await fetchAPI('/indexers', api_key);
        indexers = response.result || [];
        renderIndexers(api_key);
    } catch (error) {
        console.error('Failed to load indexers:', error);
    }
}

function renderIndexers(api_key) {
    const list = document.getElementById('indexer-list');
    // Remove existing indexer buttons (keep the add button)
    const existingButtons = list.querySelectorAll('.indexer-button');
    existingButtons.forEach(btn => btn.remove());
    
    const addButton = document.getElementById('add-indexer');
    
    if (Array.isArray(indexers)) {
        indexers.forEach(indexer => {
            const button = document.createElement('button');
            button.className = 'indexer-button';
            button.textContent = indexer.title;
            button.dataset.id = indexer.id;
            button.addEventListener('click', () => openEditWindow(indexer));
            list.insertBefore(button, addButton);
        });
    }
}

function setupEventListeners(api_key) {
    // Add indexer button
    document.getElementById('add-indexer').addEventListener('click', openAddWindow);
    
    // Submit add button
    document.getElementById('submit-indexer-add').addEventListener('click', async (e) => {
        e.preventDefault();
        await addIndexer(api_key);
    });
    
    // Test add button
    document.getElementById('test-indexer-add').addEventListener('click', () => {
        testIndexer('add', api_key);
    });
    
    // Submit edit button
    document.getElementById('submit-indexer-edit').addEventListener('click', async (e) => {
        e.preventDefault();
        await updateIndexer(api_key);
    });
    
    // Test edit button
    document.getElementById('test-indexer-edit').addEventListener('click', () => {
        testIndexer('edit', api_key);
    });
    
    // Delete button
    document.getElementById('delete-indexer-edit').addEventListener('click', async () => {
        await deleteIndexer(api_key);
    });
}

function openAddWindow() {
    document.getElementById('add-name-input').value = '';
    document.getElementById('add-url-input').value = '';
    document.getElementById('add-apikey-input').value = '';
    document.getElementById('add-error').classList.add('hidden');
    resetTestButton('add');
    showWindow('add-indexer-window');
}

function openEditWindow(indexer) {
    editingIndexerId = indexer.id;
    document.getElementById('edit-name-input').value = indexer.title;
    document.getElementById('edit-url-input').value = indexer.base_url;
    document.getElementById('edit-apikey-input').value = indexer.api_key;
    document.getElementById('edit-error').classList.add('hidden');
    resetTestButton('edit');
    showWindow('edit-indexer-window');
}

async function testIndexer(mode, api_key) {
    const prefix = mode === 'add' ? 'add' : 'edit';
    const name = document.getElementById(`${prefix}-name-input`).value;
    const indexer_url = document.getElementById(`${prefix}-url-input`).value;
    const indexer_api_key = document.getElementById(`${prefix}-apikey-input`).value;
    
    const testButton = document.getElementById(`test-indexer-${prefix}`);
    testButton.classList.remove('success', 'failed');
    testButton.classList.add('testing');
    
    try {
        await sendAPI('POST', '/indexers/test', api_key, {}, { 
            title: name,
            base_url: indexer_url,
            api_key: indexer_api_key 
        });
        testButton.classList.remove('testing');
        testButton.classList.add('success');
    } catch (error) {
        testButton.classList.remove('testing');
        testButton.classList.add('failed');
    }
}

function resetTestButton(mode) {
    const testButton = document.getElementById(`test-indexer-${mode}`);
    testButton.classList.remove('success', 'failed', 'testing');
}

async function addIndexer(api_key) {
    const name = document.getElementById('add-name-input').value;
    const indexer_url = document.getElementById('add-url-input').value;
    const indexer_api_key = document.getElementById('add-apikey-input').value;
    
    try {
        await sendAPI('POST', '/indexers', api_key, {}, { 
            title: name,
            base_url: indexer_url,
            api_key: indexer_api_key 
        });
        closeWindow();
        loadIndexers(api_key);
    } catch (error) {
        document.getElementById('add-error').textContent = error.message || 'Failed to add indexer';
        document.getElementById('add-error').classList.remove('hidden');
    }
}

async function updateIndexer(api_key) {
    const name = document.getElementById('edit-name-input').value;
    const indexer_url = document.getElementById('edit-url-input').value;
    const indexer_api_key = document.getElementById('edit-apikey-input').value;
    
    try {
        await sendAPI('PUT', `/indexers/${editingIndexerId}`, api_key, {}, { 
            title: name,
            base_url: indexer_url,
            api_key: indexer_api_key 
        });
        closeWindow();
        loadIndexers(api_key);
    } catch (error) {
        document.getElementById('edit-error').textContent = error.message || 'Failed to update indexer';
        document.getElementById('edit-error').classList.remove('hidden');
    }
}

async function deleteIndexer(api_key) {
    if (!confirm('Are you sure you want to delete this indexer?')) return;
    
    try {
        await sendAPI('DELETE', `/indexers/${editingIndexerId}`, api_key);
        closeWindow();
        loadIndexers(api_key);
    } catch (error) {
        console.error('Failed to delete indexer:', error);
    }
}
