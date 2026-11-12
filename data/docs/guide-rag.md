# Guide : qu'est-ce que le RAG ?

Le RAG (Retrieval-Augmented Generation, ou génération augmentée par la recherche) est une technique qui permet à un modèle de langage de répondre à partir de documents qu'il n'a jamais vus pendant son entraînement.

## Le principe en trois temps

1. Indexation : on découpe les documents en passages courts (chunks), puis on transforme chaque passage en vecteur numérique (embedding) qui représente son sens.
2. Recherche : quand l'utilisateur pose une question, on la transforme aussi en vecteur et on cherche les passages dont les vecteurs sont les plus proches.
3. Génération : on envoie au modèle la question accompagnée des passages retrouvés, et on lui demande de répondre uniquement à partir de ces passages, en citant ses sources.

## Pourquoi c'est utile

Le RAG réduit les hallucinations, car le modèle s'appuie sur des faits fournis. Il permet aussi de travailler sur des données privées ou récentes sans réentraîner le modèle, et de mettre à jour la base de connaissances simplement en ajoutant des documents.

## Les réglages importants

La taille des chunks est un compromis : trop petits, ils perdent le contexte ; trop grands, la recherche devient moins précise. Une valeur de 500 à 1500 caractères avec un recouvrement de 10 à 20 % est un bon point de départ.

Le nombre de passages envoyés au modèle (top-k) se situe en général entre 3 et 8.
